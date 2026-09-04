#include "team_peer_directory.h"

#include <algorithm>

#include "team_communication_protocol.h"

namespace {

constexpr uint8_t BOOT_CONFIRMATION_DISCOVERIES = 3;
constexpr auto BOOT_CONFIRMATION_WINDOW = std::chrono::seconds(5);
constexpr std::size_t MAX_RETIRED_BOOT_IDS = 8;

} // namespace

TeamPeerDirectory::DiscoveryResult TeamPeerDirectory::observe(
    const TeamDiscoveryMsg &message,
    uint32_t sourceIp,
    Clock::time_point now)
{
    std::lock_guard<std::mutex> lock(mutex_);
    State &state = peers_[message.playerId];
    if (state.endpoint.bootId == 0) {
        state.endpoint = {
            message.playerId,
            message.bootId,
            sourceIp,
            message.statePort,
            now,
        };
        state.discoverySequence = message.sequence;
        state.stateSequenceValid = false;
        return DiscoveryResult::Added;
    }

    if (message.bootId == state.endpoint.bootId) {
        const bool discoveryNewer =
            team_communication_protocol::sequenceIsNewer(
                message.sequence, state.discoverySequence);
        if (discoveryNewer) {
            state.discoverySequence = message.sequence;
            state.endpoint.ip = sourceIp;
            state.endpoint.port = message.statePort;
            state.endpoint.lastSeen = now;
        }
        state.pendingBootId = 0;
        state.pendingCount = 0;
        state.pendingFirstSeen = {};
        return discoveryNewer
            ? DiscoveryResult::Refreshed : DiscoveryResult::Stale;
    }

    if (std::find(
            state.retiredBootIds.begin(), state.retiredBootIds.end(),
            message.bootId) != state.retiredBootIds.end()) {
        return DiscoveryResult::RetiredBoot;
    }

    const bool sameCandidate = state.pendingBootId == message.bootId &&
        state.pendingIp == sourceIp &&
        state.pendingPort == message.statePort &&
        state.pendingFirstSeen != Clock::time_point{} &&
        now - state.pendingFirstSeen <= BOOT_CONFIRMATION_WINDOW;
    if (sameCandidate) {
        if (!team_communication_protocol::sequenceIsNewer(
                message.sequence, state.pendingSequence)) {
            return DiscoveryResult::Stale;
        }
        state.pendingSequence = message.sequence;
        state.pendingIp = sourceIp;
        state.pendingPort = message.statePort;
        ++state.pendingCount;
    } else {
        state.pendingBootId = message.bootId;
        state.pendingSequence = message.sequence;
        state.pendingCount = 1;
        state.pendingIp = sourceIp;
        state.pendingPort = message.statePort;
        state.pendingFirstSeen = now;
    }
    if (state.pendingCount < BOOT_CONFIRMATION_DISCOVERIES) {
        return DiscoveryResult::PendingBoot;
    }

    state.retiredBootIds.push_back(state.endpoint.bootId);
    if (state.retiredBootIds.size() > MAX_RETIRED_BOOT_IDS) {
        state.retiredBootIds.erase(state.retiredBootIds.begin());
    }
    state.endpoint = {
        message.playerId,
        state.pendingBootId,
        state.pendingIp,
        state.pendingPort,
        now,
    };
    state.discoverySequence = state.pendingSequence;
    state.stateSequenceValid = false;
    state.pendingBootId = 0;
    state.pendingCount = 0;
    state.pendingFirstSeen = {};
    return DiscoveryResult::BootChanged;
}

bool TeamPeerDirectory::acceptNewStateSource(
    uint8_t playerId,
    uint64_t bootId,
    uint32_t sequence,
    uint32_t sourceIp,
    uint16_t sourcePort,
    Clock::time_point now)
{
    std::lock_guard<std::mutex> lock(mutex_);
    auto found = peers_.find(playerId);
    if (found == peers_.end()) return false;
    Endpoint &endpoint = found->second.endpoint;
    if (endpoint.bootId != bootId || endpoint.ip != sourceIp ||
        endpoint.port != sourcePort) {
        return false;
    }
    State &state = found->second;
    if (state.stateSequenceValid &&
        !team_communication_protocol::sequenceIsNewer(
            sequence, state.stateSequence)) {
        return false;
    }
    state.stateSequence = sequence;
    state.stateSequenceValid = true;
    state.pendingBootId = 0;
    state.pendingCount = 0;
    state.pendingFirstSeen = {};
    endpoint.lastSeen = now;
    return true;
}

std::vector<TeamPeerDirectory::Endpoint> TeamPeerDirectory::activeEndpoints(
    Clock::time_point now,
    std::chrono::milliseconds timeout)
{
    std::vector<Endpoint> result;
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto &entry : peers_) {
        if (now - entry.second.endpoint.lastSeen <= timeout) {
            result.push_back(entry.second.endpoint);
        }
    }
    return result;
}

void TeamPeerDirectory::clear()
{
    std::lock_guard<std::mutex> lock(mutex_);
    peers_.clear();
}
