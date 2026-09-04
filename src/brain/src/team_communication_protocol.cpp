#include "team_communication_protocol.h"

#include <algorithm>
#include <cstring>
#include <limits>
#include <type_traits>

namespace team_communication_protocol {
namespace {

constexpr std::size_t HEADER_BYTES = 8;
constexpr std::size_t TRAILER_BYTES = 12; // CRC32 followed by SipHash-2-4.

class Writer
{
public:
    void u8(uint8_t value) { bytes.push_back(value); }

    void i8(int8_t value) { u8(static_cast<uint8_t>(value)); }

    void u16(uint16_t value)
    {
        u8(static_cast<uint8_t>(value >> 8));
        u8(static_cast<uint8_t>(value));
    }

    void u32(uint32_t value)
    {
        for (int shift = 24; shift >= 0; shift -= 8) {
            u8(static_cast<uint8_t>(value >> shift));
        }
    }

    void u64(uint64_t value)
    {
        for (int shift = 56; shift >= 0; shift -= 8) {
            u8(static_cast<uint8_t>(value >> shift));
        }
    }

    void f64(double value)
    {
        static_assert(sizeof(double) == sizeof(uint64_t));
        static_assert(std::numeric_limits<double>::is_iec559);
        uint64_t bits = 0;
        std::memcpy(&bits, &value, sizeof(bits));
        u64(bits);
    }

    void patchU16(std::size_t offset, uint16_t value)
    {
        bytes[offset] = static_cast<uint8_t>(value >> 8);
        bytes[offset + 1] = static_cast<uint8_t>(value);
    }

    std::vector<uint8_t> bytes;
};

class Reader
{
public:
    Reader(const uint8_t *input, std::size_t inputSize)
        : data(input), size(inputSize) {}

    bool u8(uint8_t &value)
    {
        if (offset >= size) return false;
        value = data[offset++];
        return true;
    }

    bool i8(int8_t &value)
    {
        uint8_t raw = 0;
        if (!u8(raw)) return false;
        value = static_cast<int8_t>(raw);
        return true;
    }

    bool u16(uint16_t &value)
    {
        uint8_t high = 0;
        uint8_t low = 0;
        if (!u8(high) || !u8(low)) return false;
        value = static_cast<uint16_t>(
            (static_cast<uint16_t>(high) << 8) | low);
        return true;
    }

    bool u32(uint32_t &value)
    {
        value = 0;
        for (int i = 0; i < 4; ++i) {
            uint8_t byte = 0;
            if (!u8(byte)) return false;
            value = (value << 8) | byte;
        }
        return true;
    }

    bool u64(uint64_t &value)
    {
        value = 0;
        for (int i = 0; i < 8; ++i) {
            uint8_t byte = 0;
            if (!u8(byte)) return false;
            value = (value << 8) | byte;
        }
        return true;
    }

    bool f64(double &value)
    {
        uint64_t bits = 0;
        if (!u64(bits)) return false;
        std::memcpy(&value, &bits, sizeof(value));
        return true;
    }

    bool finished() const { return offset == size; }

private:
    const uint8_t *data;
    std::size_t size;
    std::size_t offset = 0;
};

uint64_t rotateLeft(uint64_t value, int bits)
{
    return (value << bits) | (value >> (64 - bits));
}

uint64_t loadLittleEndian64(const uint8_t *data)
{
    uint64_t value = 0;
    for (int i = 7; i >= 0; --i) value = (value << 8) | data[i];
    return value;
}

uint64_t sipHash24(
    const uint8_t *data,
    std::size_t size,
    const AuthenticationKey &key)
{
    const uint64_t k0 = loadLittleEndian64(key.data());
    const uint64_t k1 = loadLittleEndian64(key.data() + 8);
    uint64_t v0 = 0x736f6d6570736575ULL ^ k0;
    uint64_t v1 = 0x646f72616e646f6dULL ^ k1;
    uint64_t v2 = 0x6c7967656e657261ULL ^ k0;
    uint64_t v3 = 0x7465646279746573ULL ^ k1;

    const auto round = [&]() {
        v0 += v1;
        v1 = rotateLeft(v1, 13);
        v1 ^= v0;
        v0 = rotateLeft(v0, 32);
        v2 += v3;
        v3 = rotateLeft(v3, 16);
        v3 ^= v2;
        v0 += v3;
        v3 = rotateLeft(v3, 21);
        v3 ^= v0;
        v2 += v1;
        v1 = rotateLeft(v1, 17);
        v1 ^= v2;
        v2 = rotateLeft(v2, 32);
    };

    const uint8_t *cursor = data;
    const uint8_t *end = data + (size & ~std::size_t{7});
    while (cursor != end) {
        const uint64_t block = loadLittleEndian64(cursor);
        v3 ^= block;
        round();
        round();
        v0 ^= block;
        cursor += 8;
    }

    uint64_t tail = static_cast<uint64_t>(size) << 56;
    for (std::size_t i = 0; i < (size & 7); ++i) {
        tail |= static_cast<uint64_t>(cursor[i]) << (8 * i);
    }
    v3 ^= tail;
    round();
    round();
    v0 ^= tail;
    v2 ^= 0xff;
    round();
    round();
    round();
    round();
    return v0 ^ v1 ^ v2 ^ v3;
}

uint32_t crc32(const uint8_t *data, std::size_t size)
{
    uint32_t crc = 0xffffffffU;
    for (std::size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            const uint32_t mask = 0U - (crc & 1U);
            crc = (crc >> 1) ^ (0xedb88320U & mask);
        }
    }
    return ~crc;
}

void finishPacket(Writer &writer, const AuthenticationKey &key)
{
    const std::size_t totalSize = writer.bytes.size() + TRAILER_BYTES;
    writer.patchU16(6, static_cast<uint16_t>(totalSize));
    writer.u32(crc32(writer.bytes.data(), writer.bytes.size()));
    writer.u64(sipHash24(writer.bytes.data(), writer.bytes.size(), key));
}

bool constantTimeEqual64(uint64_t lhs, uint64_t rhs)
{
    uint64_t difference = lhs ^ rhs;
    difference |= difference >> 32;
    difference |= difference >> 16;
    difference |= difference >> 8;
    return static_cast<uint8_t>(difference) == 0;
}

bool validatePacket(
    const uint8_t *data,
    std::size_t size,
    uint32_t expectedMagic,
    const AuthenticationKey &key,
    std::string *error)
{
    const auto fail = [&](const char *message) {
        if (error != nullptr) *error = message;
        return false;
    };
    if (data == nullptr || size < HEADER_BYTES + TRAILER_BYTES ||
        size > MAX_PACKET_BYTES) {
        return fail("invalid packet size");
    }
    Reader header(data, HEADER_BYTES);
    uint32_t magic = 0;
    uint16_t version = 0;
    uint16_t payloadBytes = 0;
    if (!header.u32(magic) || !header.u16(version) ||
        !header.u16(payloadBytes)) {
        return fail("truncated packet header");
    }
    if (magic != expectedMagic) return fail("invalid packet magic");
    if (version != TEAM_COMMUNICATION_PROTOCOL_VERSION) {
        return fail("unsupported protocol version");
    }
    if (payloadBytes != size) return fail("packet length mismatch");

    Reader trailer(data + size - TRAILER_BYTES, TRAILER_BYTES);
    uint32_t receivedCrc = 0;
    uint64_t receivedMac = 0;
    if (!trailer.u32(receivedCrc) || !trailer.u64(receivedMac)) {
        return fail("truncated packet trailer");
    }
    const uint32_t expectedCrc = crc32(data, size - TRAILER_BYTES);
    if (receivedCrc != expectedCrc) return fail("packet CRC mismatch");
    const uint64_t expectedMac = sipHash24(data, size - 8, key);
    if (!constantTimeEqual64(receivedMac, expectedMac)) {
        return fail("packet authentication failed");
    }
    return true;
}

int hexValue(char value)
{
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
}

void writeHeader(Writer &writer, uint32_t magic)
{
    writer.u32(magic);
    writer.u16(TEAM_COMMUNICATION_PROTOCOL_VERSION);
    writer.u16(0);
}

} // namespace

bool parseAuthenticationKey(
    const std::string &hex,
    AuthenticationKey &key,
    std::string *error)
{
    if (hex.size() != key.size() * 2) {
        if (error != nullptr) {
            *error = "communication.team_secret_hex must contain exactly 32 hex characters";
        }
        return false;
    }
    bool anyNonZero = false;
    for (std::size_t i = 0; i < key.size(); ++i) {
        const int high = hexValue(hex[i * 2]);
        const int low = hexValue(hex[i * 2 + 1]);
        if (high < 0 || low < 0) {
            if (error != nullptr) {
                *error = "communication.team_secret_hex contains a non-hex character";
            }
            return false;
        }
        key[i] = static_cast<uint8_t>((high << 4) | low);
        anyNonZero = anyNonZero || key[i] != 0;
    }
    if (!anyNonZero) {
        if (error != nullptr) {
            *error = "communication.team_secret_hex must not be all zeros";
        }
        return false;
    }
    return true;
}

std::vector<uint8_t> encodeDiscovery(
    const TeamDiscoveryMsg &message,
    const AuthenticationKey &key)
{
    Writer writer;
    writeHeader(writer, DISCOVERY_MAGIC);
    writer.u64(message.bootId);
    writer.u32(message.sequence);
    writer.u16(message.teamId);
    writer.u8(message.playerId);
    writer.u16(message.statePort);
    finishPacket(writer, key);
    return writer.bytes;
}

bool decodeDiscovery(
    const uint8_t *data,
    std::size_t size,
    const AuthenticationKey &key,
    TeamDiscoveryMsg &message,
    std::string *error)
{
    if (!validatePacket(data, size, DISCOVERY_MAGIC, key, error)) return false;
    Reader reader(data + HEADER_BYTES, size - HEADER_BYTES - TRAILER_BYTES);
    return reader.u64(message.bootId) &&
        reader.u32(message.sequence) &&
        reader.u16(message.teamId) &&
        reader.u8(message.playerId) &&
        reader.u16(message.statePort) && reader.finished();
}

std::vector<uint8_t> encodeState(
    const TeamCommunicationMsg &message,
    const AuthenticationKey &key)
{
    Writer writer;
    writeHeader(writer, STATE_MAGIC);
    writer.u64(message.bootId);
    writer.u32(message.sequence);
    writer.u16(message.teamId);
    writer.u8(message.playerId);
    writer.u8(message.playerRole);
    writer.u8(message.playerStartRole);
    uint8_t flags = 0;
    flags |= message.isAlive ? 1U << 0 : 0;
    flags |= message.isLead ? 1U << 1 : 0;
    flags |= message.ballDetected ? 1U << 2 : 0;
    flags |= message.ballLocationKnown ? 1U << 3 : 0;
    flags |= message.kickoffActive ? 1U << 4 : 0;
    flags |= message.freeKickOurRestart ? 1U << 5 : 0;
    writer.u8(flags);
    writer.f64(message.ballConfidence);
    writer.f64(message.ballRange);
    writer.f64(message.cost);
    writer.f64(message.ballPosToField.x);
    writer.f64(message.ballPosToField.y);
    writer.f64(message.ballPosToField.z);
    writer.f64(message.robotPoseToField.x);
    writer.f64(message.robotPoseToField.y);
    writer.f64(message.robotPoseToField.theta);
    writer.f64(message.kickDir);
    writer.f64(message.thetaRb);
    writer.u8(message.ballOwnerId);
    writer.u32(message.leaderTerm);
    writer.u8(message.formationOwnerId);
    writer.u32(message.formationRevision);
    writer.i8(message.formationShadowSide);
    for (int8_t slot : message.assistSlots) writer.i8(slot);
    writer.i8(message.assistSlot);
    writer.i8(message.assistPhase);
    writer.f64(message.assistTarget.x);
    writer.f64(message.assistTarget.y);
    writer.u8(message.kickoffOwnerId);
    writer.u32(message.kickoffOwnerTerm);
    writer.u64(message.freeKickProposerBootId);
    writer.u32(message.freeKickRefereePacketNumber);
    writer.u32(message.freeKickEventId);
    writer.u32(message.freeKickLeaderTerm);
    writer.u8(message.freeKickType);
    writer.u8(message.freeKickPhase);
    writer.u8(message.freeKickKickerId);
    writer.u8(message.freeKickReceiverId);
    writer.f64(message.freeKickBall.x);
    writer.f64(message.freeKickBall.y);
    writer.f64(message.freeKickBall.z);
    writer.f64(message.freeKickPassTarget.x);
    writer.f64(message.freeKickPassTarget.y);
    for (int8_t slot : message.freeKickAssistSlots) writer.i8(slot);
    writer.u32(message.freeKickExecutionElapsedMs);
    finishPacket(writer, key);
    return writer.bytes;
}

bool decodeState(
    const uint8_t *data,
    std::size_t size,
    const AuthenticationKey &key,
    TeamCommunicationMsg &message,
    std::string *error)
{
    if (!validatePacket(data, size, STATE_MAGIC, key, error)) return false;
    Reader reader(data + HEADER_BYTES, size - HEADER_BYTES - TRAILER_BYTES);
    uint8_t flags = 0;
    bool ok = reader.u64(message.bootId) &&
        reader.u32(message.sequence) && reader.u16(message.teamId) &&
        reader.u8(message.playerId) && reader.u8(message.playerRole) &&
        reader.u8(message.playerStartRole) && reader.u8(flags);
    if (!ok || (flags & 0xc0U) != 0) {
        if (error != nullptr) *error = "invalid state flags";
        return false;
    }
    message.isAlive = (flags & (1U << 0)) != 0;
    message.isLead = (flags & (1U << 1)) != 0;
    message.ballDetected = (flags & (1U << 2)) != 0;
    message.ballLocationKnown = (flags & (1U << 3)) != 0;
    message.kickoffActive = (flags & (1U << 4)) != 0;
    message.freeKickOurRestart = (flags & (1U << 5)) != 0;

    ok = reader.f64(message.ballConfidence) &&
        reader.f64(message.ballRange) && reader.f64(message.cost) &&
        reader.f64(message.ballPosToField.x) &&
        reader.f64(message.ballPosToField.y) &&
        reader.f64(message.ballPosToField.z) &&
        reader.f64(message.robotPoseToField.x) &&
        reader.f64(message.robotPoseToField.y) &&
        reader.f64(message.robotPoseToField.theta) &&
        reader.f64(message.kickDir) && reader.f64(message.thetaRb) &&
        reader.u8(message.ballOwnerId) && reader.u32(message.leaderTerm) &&
        reader.u8(message.formationOwnerId) &&
        reader.u32(message.formationRevision) &&
        reader.i8(message.formationShadowSide);
    for (int8_t &slot : message.assistSlots) ok = ok && reader.i8(slot);
    ok = ok && reader.i8(message.assistSlot) &&
        reader.i8(message.assistPhase) &&
        reader.f64(message.assistTarget.x) &&
        reader.f64(message.assistTarget.y) &&
        reader.u8(message.kickoffOwnerId) &&
        reader.u32(message.kickoffOwnerTerm) &&
        reader.u64(message.freeKickProposerBootId) &&
        reader.u32(message.freeKickRefereePacketNumber) &&
        reader.u32(message.freeKickEventId) &&
        reader.u32(message.freeKickLeaderTerm) &&
        reader.u8(message.freeKickType) &&
        reader.u8(message.freeKickPhase) &&
        reader.u8(message.freeKickKickerId) &&
        reader.u8(message.freeKickReceiverId) &&
        reader.f64(message.freeKickBall.x) &&
        reader.f64(message.freeKickBall.y) &&
        reader.f64(message.freeKickBall.z) &&
        reader.f64(message.freeKickPassTarget.x) &&
        reader.f64(message.freeKickPassTarget.y);
    for (int8_t &slot : message.freeKickAssistSlots) {
        ok = ok && reader.i8(slot);
    }
    ok = ok && reader.u32(message.freeKickExecutionElapsedMs);
    if (!ok || !reader.finished()) {
        if (error != nullptr) *error = "state payload layout mismatch";
        return false;
    }
    return true;
}

bool sequenceIsNewer(uint32_t candidate, uint32_t reference)
{
    const uint32_t distance = candidate - reference;
    return distance != 0 && distance < 0x80000000U;
}

} // namespace team_communication_protocol
