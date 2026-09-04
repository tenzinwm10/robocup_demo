#pragma once

#include <atomic>
#include <cstddef>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <thread>

#include <netinet/in.h>

#include "RoboCupGameControlData.h"
#include "team_communication_protocol.h"
#include "team_peer_directory.h"

class Brain;

class BrainCommunication
{
public:
    explicit BrainCommunication(Brain *brain);
    ~BrainCommunication();

    void initCommunication();
    uint64_t bootId() const { return bootId_; }

private:
    Brain *brain_;
    uint64_t bootId_ = 0;

    std::mutex wakeMutex_;
    std::condition_variable wakeCv_;

    std::atomic_bool gameControllerRunning_{false};
    std::thread gameControllerThread_;
    int gameControllerSocket_ = -1;
    sockaddr_in gameControllerAddress_{};

    std::atomic_bool discoverySendRunning_{false};
    std::atomic_bool discoveryReceiveRunning_{false};
    std::thread discoverySendThread_;
    std::thread discoveryReceiveThread_;
    int discoverySendSocket_ = -1;
    int discoveryReceiveSocket_ = -1;
    sockaddr_in discoveryAddress_{};
    uint16_t discoveryPort_ = 0;
    uint32_t discoverySequence_ = 0;

    std::atomic_bool stateSendRunning_{false};
    std::thread stateSendThread_;
    int stateSocket_ = -1;
    uint16_t statePort_ = 0;
    uint32_t stateSequence_ = 0;
    int stateSendIntervalMs_ = 50;

    team_communication_protocol::AuthenticationKey authenticationKey_{};
    TeamPeerDirectory peerDirectory_;

    void initGameControllerUnicast();
    void stopGameControllerUnicast();
    void sendToGameController();

    bool initTeamCommunication();
    void stopTeamCommunication();
    void sendDiscovery();
    void receiveDiscovery();
    void sendState();
    void processStatePacket(
        const uint8_t *data,
        std::size_t size,
        const sockaddr_in &source);

    int teamCommunicationIntervalMs() const;
    double effectiveTacticalTimeoutMs(int intervalMs) const;
    static uint16_t discoveryPortForTeam(int teamId);
    static uint16_t statePortForPlayer(int teamId, int playerId);

    static constexpr int GAME_CONTROLLER_INTERVAL_MS = 1000;
    static constexpr int DISCOVERY_INTERVAL_MS = 500;
    static constexpr int SOCKET_RECEIVE_TIMEOUT_MS = 250;
    static constexpr double DEFAULT_TEAM_RATE_HZ = 20.0;
    static constexpr double MIN_TEAM_RATE_HZ = 0.1;
    static constexpr double MAX_TEAM_RATE_HZ = 20.0;
};
