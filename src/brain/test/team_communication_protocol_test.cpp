#include "team_communication_protocol.h"
#include "team_peer_directory.h"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace {

int failures = 0;

void expect(bool condition, const std::string &message)
{
    if (condition) return;
    ++failures;
    std::cerr << "FAIL: " << message << '\n';
}

team_communication_protocol::AuthenticationKey testKey()
{
    team_communication_protocol::AuthenticationKey key{};
    std::string error;
    expect(
        team_communication_protocol::parseAuthenticationKey(
            "00112233445566778899aabbccddeeff", key, &error),
        "valid authentication key should parse: " + error);
    return key;
}

TeamCommunicationMsg sampleState()
{
    TeamCommunicationMsg message;
    message.bootId = 0x1020304050607080ULL;
    message.sequence = 0xfffffffeU;
    message.teamId = 29;
    message.playerId = 3;
    message.playerRole = 1;
    message.playerStartRole = 2;
    message.isAlive = true;
    message.isLead = true;
    message.ballDetected = true;
    message.ballLocationKnown = true;
    message.ballConfidence = 87.5;
    message.ballRange = 2.25;
    message.cost = 3.5;
    message.ballPosToField = {1.5, -2.0, 0.1};
    message.robotPoseToField = {-3.0, 4.0, -1.2};
    message.kickDir = 0.75;
    message.thetaRb = -0.5;
    message.ballOwnerId = 3;
    message.leaderTerm = 17;
    message.formationOwnerId = 3;
    message.formationRevision = 42;
    message.formationShadowSide = -1;
    message.assistSlots[0] = 1;
    message.assistSlots[1] = 2;
    message.assistSlot = 3;
    message.assistPhase = 2;
    message.assistTarget = {2.0, 1.0};
    message.kickoffActive = true;
    message.kickoffOwnerId = 3;
    message.kickoffOwnerTerm = 18;
    message.freeKickProposerBootId = 0xaabbccddeeff0011ULL;
    message.freeKickRefereePacketNumber = 0x12345678U;
    message.freeKickEventId = 9;
    message.freeKickLeaderTerm = 21;
    message.freeKickType = 2;
    message.freeKickPhase = 5;
    message.freeKickOurRestart = true;
    message.freeKickKickerId = 3;
    message.freeKickReceiverId = 4;
    message.freeKickBall = {-1.0, 2.5, 0.0};
    message.freeKickPassTarget = {4.5, -3.5};
    message.freeKickAssistSlots[4] = 4;
    message.freeKickExecutionElapsedMs = 2345;
    return message;
}

void testKeyParsing()
{
    team_communication_protocol::AuthenticationKey key{};
    std::string error;
    expect(
        !team_communication_protocol::parseAuthenticationKey(
            "1234", key, &error),
        "short key must be rejected");
    expect(
        !team_communication_protocol::parseAuthenticationKey(
            "00000000000000000000000000000000", key, &error),
        "zero key must be rejected");
    expect(
        !team_communication_protocol::parseAuthenticationKey(
            "00112233445566778899aabbccddeefg", key, &error),
        "non-hex key must be rejected");
}

void testDiscoveryRoundTrip()
{
    const auto key = testKey();
    TeamDiscoveryMsg sent;
    sent.bootId = 123456789;
    sent.sequence = 77;
    sent.teamId = 29;
    sent.playerId = 4;
    sent.statePort = 30584;
    const auto packet = team_communication_protocol::encodeDiscovery(sent, key);
    expect(
        packet.size() <= team_communication_protocol::MAX_PACKET_BYTES,
        "discovery packet must fit the official GameController team channel");
    const std::vector<uint8_t> expectedPacket{
        0x54, 0x44, 0x32, 0x44, 0x00, 0x06, 0x00, 0x25,
        0x00, 0x00, 0x00, 0x00, 0x07, 0x5b, 0xcd, 0x15,
        0x00, 0x00, 0x00, 0x4d, 0x00, 0x1d, 0x04, 0x77,
        0x78, 0x48, 0xdf, 0xca, 0x63, 0xca, 0x39, 0x02,
        0xf2, 0xd8, 0x8c, 0x62, 0x9f,
    };
    expect(
        packet == expectedPacket,
        "discovery wire format should match the protocol v6 golden packet");

    TeamDiscoveryMsg received;
    std::string error;
    expect(
        team_communication_protocol::decodeDiscovery(
            packet.data(), packet.size(), key, received, &error),
        "discovery packet should decode: " + error);
    expect(received.bootId == sent.bootId, "discovery boot ID round trip");
    expect(received.sequence == sent.sequence, "discovery sequence round trip");
    expect(received.teamId == sent.teamId, "discovery team round trip");
    expect(received.playerId == sent.playerId, "discovery player round trip");
    expect(received.statePort == sent.statePort, "discovery port round trip");
}

void testStateRoundTripAndIntegrity()
{
    const auto key = testKey();
    const TeamCommunicationMsg sent = sampleState();
    auto packet = team_communication_protocol::encodeState(sent, key);
    expect(packet.size() == 268, "protocol v6 state packet size");
    expect(
        packet.size() <= team_communication_protocol::MAX_PACKET_BYTES,
        "state packet must fit the official GameController team channel");

    TeamCommunicationMsg received;
    std::string error;
    expect(
        team_communication_protocol::decodeState(
            packet.data(), packet.size(), key, received, &error),
        "state packet should decode: " + error);
    expect(received.bootId == sent.bootId, "state boot ID round trip");
    expect(received.sequence == sent.sequence, "state sequence round trip");
    expect(received.playerId == sent.playerId, "state player round trip");
    expect(received.playerStartRole == sent.playerStartRole, "start role round trip");
    expect(received.freeKickOurRestart, "boolean flags round trip");
    expect(
        std::fabs(received.ballPosToField.y - sent.ballPosToField.y) < 1e-12,
        "floating-point state round trip");
    expect(
        received.freeKickExecutionElapsedMs ==
            sent.freeKickExecutionElapsedMs,
        "tail field round trip");
    expect(
        received.freeKickRefereePacketNumber ==
            sent.freeKickRefereePacketNumber,
        "32-bit referee packet number round trip");

    packet[20] ^= 0x40;
    expect(
        !team_communication_protocol::decodeState(
            packet.data(), packet.size(), key, received, &error),
        "corrupted state packet must be rejected");

    auto wrongKey = key;
    wrongKey[0] ^= 0xff;
    packet = team_communication_protocol::encodeState(sent, key);
    expect(
        !team_communication_protocol::decodeState(
            packet.data(), packet.size(), wrongKey, received, &error),
        "state packet with wrong team key must be rejected");

    std::vector<uint8_t> oversized(
        team_communication_protocol::MAX_PACKET_BYTES + 1, 0);
    expect(
        !team_communication_protocol::decodeState(
            oversized.data(), oversized.size(), key, received, &error),
        "packet above the official 512-byte limit must be rejected");
}

void testSequenceWrap()
{
    expect(
        team_communication_protocol::sequenceIsNewer(0, 0xffffffffU),
        "sequence wrap should advance");
    expect(
        !team_communication_protocol::sequenceIsNewer(10, 10),
        "duplicate sequence should be stale");
    expect(
        !team_communication_protocol::sequenceIsNewer(9, 10),
        "older sequence should be stale");
}

void testPeerDirectory()
{
    TeamPeerDirectory directory;
    const auto now = TeamPeerDirectory::Clock::now();
    TeamDiscoveryMsg first;
    first.bootId = 100;
    first.sequence = 1;
    first.playerId = 2;
    first.statePort = 30582;
    expect(
        directory.observe(first, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::Added,
        "first discovery should add peer");
    expect(
        directory.acceptNewStateSource(
            2, 100, 1, 0x01020304U, 30582, now),
        "state source should match discovered endpoint");
    expect(
        !directory.acceptNewStateSource(
            2, 100, 2, 0x01020305U, 30582, now),
        "different source IP should not match");

    first.sequence = 2;
    first.statePort = 30583;
    expect(
        directory.observe(first, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::Refreshed,
        "same boot should refresh endpoint");
    expect(
        directory.acceptNewStateSource(
            2, 100, 2, 0x01020304U, 30583, now),
        "refreshed port should replace old endpoint");

    TeamDiscoveryMsg restarted = first;
    restarted.bootId = 200;
    restarted.sequence = 1;
    expect(
        directory.observe(restarted, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::PendingBoot,
        "single new-boot discovery should remain pending");
    first.sequence = 2;
    expect(
        directory.observe(first, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::Stale,
        "duplicate current-boot discovery should remain stale");
    restarted.sequence = 2;
    expect(
        directory.observe(restarted, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::PendingBoot,
        "current-boot discovery should reset restart confirmation");
    expect(
        directory.acceptNewStateSource(
            2, 100, 3, 0x01020304U, 30583, now),
        "current boot state should remain valid during restart confirmation");
    restarted.sequence = 3;
    expect(
        directory.observe(restarted, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::PendingBoot,
        "current boot state should also reset restart confirmation");
    restarted.sequence = 4;
    expect(
        directory.observe(restarted, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::PendingBoot,
        "second consecutive discovery should remain pending");
    restarted.sequence = 5;
    expect(
        directory.observe(restarted, 0x01020304U, now) ==
            TeamPeerDirectory::DiscoveryResult::BootChanged,
        "third advancing discovery should confirm restart");
    expect(
        directory.acceptNewStateSource(
            2, 200, 1, 0x01020304U, 30583, now),
        "new boot should own endpoint after confirmation");

    expect(
        !directory.acceptNewStateSource(
            2, 200, 1, 0x01020304U, 30583,
            now + std::chrono::seconds(4)),
        "duplicate state must not renew endpoint lease");
    expect(
        directory.acceptNewStateSource(
            2, 200, 2, 0x01020304U, 30583,
            now + std::chrono::seconds(4)),
        "newer state should renew endpoint lease");
    const auto renewed = directory.activeEndpoints(
        now + std::chrono::seconds(6), std::chrono::seconds(5));
    expect(renewed.size() == 1, "state-renewed peer should remain active");

    const auto expired = directory.activeEndpoints(
        now + std::chrono::seconds(10), std::chrono::seconds(5));
    expect(expired.empty(), "expired peer should be excluded from sends");
}

} // namespace

int main()
{
    testKeyParsing();
    testDiscoveryRoundTrip();
    testStateRoundTripAndIntegrity();
    testSequenceWrap();
    testPeerDirectory();
    if (failures != 0) {
        std::cerr << failures << " teammate communication test(s) failed\n";
        return 1;
    }
    std::cout << "teammate communication tests passed\n";
    return 0;
}
