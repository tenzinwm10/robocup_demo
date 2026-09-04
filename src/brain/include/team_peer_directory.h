#pragma once

#include <chrono>
#include <cstdint>
#include <map>
#include <mutex>
#include <vector>

#include "team_communication_msg.h"

class TeamPeerDirectory
{
public:
    using Clock = std::chrono::steady_clock;

    struct Endpoint
    {
        uint8_t playerId = 0;
        uint64_t bootId = 0;
        uint32_t ip = 0;
        uint16_t port = 0;
        Clock::time_point lastSeen;
    };

    enum class DiscoveryResult {
        Added,
        Refreshed,
        BootChanged,
        PendingBoot,
        Stale,
        RetiredBoot,
    };

    DiscoveryResult observe(
        const TeamDiscoveryMsg &message,
        uint32_t sourceIp,
        Clock::time_point now);

    bool acceptNewStateSource(
        uint8_t playerId,
        uint64_t bootId,
        uint32_t sequence,
        uint32_t sourceIp,
        uint16_t sourcePort,
        Clock::time_point now);

    std::vector<Endpoint> activeEndpoints(
        Clock::time_point now,
        std::chrono::milliseconds timeout);

    void clear();

private:
    struct State
    {
        Endpoint endpoint;
        uint32_t discoverySequence = 0;
        bool stateSequenceValid = false;
        uint32_t stateSequence = 0;
        uint64_t pendingBootId = 0;
        uint32_t pendingSequence = 0;
        uint8_t pendingCount = 0;
        uint32_t pendingIp = 0;
        uint16_t pendingPort = 0;
        Clock::time_point pendingFirstSeen;
        std::vector<uint64_t> retiredBootIds;
    };

    mutable std::mutex mutex_;
    std::map<uint8_t, State> peers_;
};
