#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "team_communication_msg.h"

namespace team_communication_protocol {

using AuthenticationKey = std::array<uint8_t, 16>;

constexpr uint32_t DISCOVERY_MAGIC = 0x54443244U; // "TD2D"
constexpr uint32_t STATE_MAGIC = 0x54443253U;     // "TD2S"
// The official GameController accepts at most 512 bytes on the team channel.
constexpr std::size_t MAX_PACKET_BYTES = 512;

bool parseAuthenticationKey(
    const std::string &hex,
    AuthenticationKey &key,
    std::string *error = nullptr);

std::vector<uint8_t> encodeDiscovery(
    const TeamDiscoveryMsg &message,
    const AuthenticationKey &key);

bool decodeDiscovery(
    const uint8_t *data,
    std::size_t size,
    const AuthenticationKey &key,
    TeamDiscoveryMsg &message,
    std::string *error = nullptr);

std::vector<uint8_t> encodeState(
    const TeamCommunicationMsg &message,
    const AuthenticationKey &key);

bool decodeState(
    const uint8_t *data,
    std::size_t size,
    const AuthenticationKey &key,
    TeamCommunicationMsg &message,
    std::string *error = nullptr);

bool sequenceIsNewer(uint32_t candidate, uint32_t reference);

} // namespace team_communication_protocol
