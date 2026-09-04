#include "brain_communication.h"

#include "brain.h"
#include "utils/print.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <iostream>
#include <random>
#include <stdexcept>

#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <sys/socket.h>
#include <unistd.h>

namespace {

uint64_t makeBootId()
{
    std::random_device random;
    const uint64_t entropy =
        (static_cast<uint64_t>(random()) << 32) ^ random();
    const uint64_t ticks = static_cast<uint64_t>(
        std::chrono::steady_clock::now().time_since_epoch().count());
    const uint64_t pid = static_cast<uint64_t>(getpid());
    const uint64_t result = entropy ^ ticks ^ (pid << 17);
    return result == 0 ? 1 : result;
}

void closeSocket(int &socketFd)
{
    if (socketFd < 0) return;
    close(socketFd);
    socketFd = -1;
}

void setReceiveTimeout(int socketFd, int timeoutMs)
{
    const timeval timeout{
        timeoutMs / 1000,
        (timeoutMs % 1000) * 1000,
    };
    if (setsockopt(
            socketFd, SOL_SOCKET, SO_RCVTIMEO,
            &timeout, sizeof(timeout)) < 0) {
        throw std::runtime_error(
            std::string("SO_RCVTIMEO failed: ") + strerror(errno));
    }
}

std::string endpointString(uint32_t ip, uint16_t port)
{
    in_addr address{};
    address.s_addr = ip;
    char text[INET_ADDRSTRLEN]{};
    if (inet_ntop(AF_INET, &address, text, sizeof(text)) == nullptr) {
        return "invalid-address";
    }
    return std::string(text) + ":" + std::to_string(port);
}

bool directedBroadcastForTarget(
    const std::string &targetIp,
    in_addr &broadcastAddress,
    std::string &interfaceName,
    std::string &error)
{
    sockaddr_in target{};
    target.sin_family = AF_INET;
    target.sin_port = htons(GAMECONTROLLER_RETURN_PORT);
    if (inet_pton(AF_INET, targetIp.c_str(), &target.sin_addr) != 1 ||
        target.sin_addr.s_addr == htonl(INADDR_ANY) ||
        target.sin_addr.s_addr == htonl(INADDR_BROADCAST)) {
        error = "game_control_ip is not a usable unicast IPv4 address";
        return false;
    }

    const int routeSocket = socket(AF_INET, SOCK_DGRAM, 0);
    if (routeSocket < 0) {
        error = std::string("route probe socket failed: ") + strerror(errno);
        return false;
    }
    if (connect(
            routeSocket, reinterpret_cast<sockaddr *>(&target),
            sizeof(target)) < 0) {
        error = std::string("no route to game_control_ip ") + targetIp +
            ": " + strerror(errno);
        close(routeSocket);
        return false;
    }

    sockaddr_in local{};
    socklen_t localSize = sizeof(local);
    if (getsockname(
            routeSocket, reinterpret_cast<sockaddr *>(&local),
            &localSize) < 0) {
        error = std::string("route probe getsockname failed: ") +
            strerror(errno);
        close(routeSocket);
        return false;
    }
    close(routeSocket);

    ifaddrs *interfaces = nullptr;
    if (getifaddrs(&interfaces) < 0) {
        error = std::string("getifaddrs failed: ") + strerror(errno);
        return false;
    }

    bool found = false;
    for (const ifaddrs *entry = interfaces;
         entry != nullptr; entry = entry->ifa_next) {
        if (entry->ifa_addr == nullptr || entry->ifa_addr->sa_family != AF_INET ||
            (entry->ifa_flags & IFF_UP) == 0 ||
            (entry->ifa_flags & IFF_BROADCAST) == 0) {
            continue;
        }
        const auto *address = reinterpret_cast<const sockaddr_in *>(
            entry->ifa_addr);
        if (address->sin_addr.s_addr != local.sin_addr.s_addr) continue;

        if (entry->ifa_broadaddr != nullptr &&
            entry->ifa_broadaddr->sa_family == AF_INET) {
            broadcastAddress = reinterpret_cast<const sockaddr_in *>(
                entry->ifa_broadaddr)->sin_addr;
            found = true;
        } else if (entry->ifa_netmask != nullptr &&
                   entry->ifa_netmask->sa_family == AF_INET) {
            const auto *netmask = reinterpret_cast<const sockaddr_in *>(
                entry->ifa_netmask);
            broadcastAddress.s_addr = address->sin_addr.s_addr |
                ~netmask->sin_addr.s_addr;
            found = true;
        }
        if (found) {
            interfaceName = entry->ifa_name != nullptr
                ? entry->ifa_name : "unknown";
            break;
        }
    }
    freeifaddrs(interfaces);

    if (!found || broadcastAddress.s_addr == htonl(INADDR_ANY)) {
        char localText[INET_ADDRSTRLEN]{};
        inet_ntop(AF_INET, &local.sin_addr, localText, sizeof(localText));
        error = std::string("no broadcast-capable interface owns route source ") +
            localText;
        return false;
    }
    return true;
}

bool boundedValue(double value)
{
    return std::isfinite(value) && std::fabs(value) <= 1000.0;
}

bool boundedPoint(const TeamPoint3 &point)
{
    return boundedValue(point.x) && boundedValue(point.y) &&
        boundedValue(point.z);
}

bool boundedPose(const TeamPose2 &pose)
{
    return boundedValue(pose.x) && boundedValue(pose.y) &&
        boundedValue(pose.theta);
}

bool stateSemanticallyValid(
    const TeamCommunicationMsg &message,
    int playerCount)
{
    const auto validPlayer = [playerCount](uint8_t playerId) {
        return playerId <= playerCount;
    };
    const auto validSlot = [](int8_t slot) {
        return slot >= static_cast<int8_t>(AssistSlot::NONE) &&
            slot <= static_cast<int8_t>(AssistSlot::WIDE_OUTLET);
    };
    if (message.bootId == 0 || message.playerId == 0 ||
        message.playerId > playerCount ||
        (message.playerRole != 1 && message.playerRole != 2) ||
        (message.playerStartRole != 1 && message.playerStartRole != 2) ||
        !validPlayer(message.ballOwnerId) ||
        !validPlayer(message.formationOwnerId) ||
        !validPlayer(message.kickoffOwnerId) ||
        !validPlayer(message.freeKickKickerId) ||
        !validPlayer(message.freeKickReceiverId) ||
        message.formationShadowSide < -1 ||
        message.formationShadowSide > 1 ||
        message.freeKickType > 2 || message.freeKickPhase > 6 ||
        !std::isfinite(message.ballConfidence) ||
        !std::isfinite(message.ballRange) || message.ballRange < 0.0 ||
        message.ballRange > 1000.0 || !std::isfinite(message.cost) ||
        message.cost < 0.0 || message.cost > 1.0e6 ||
        !boundedPoint(message.ballPosToField) ||
        !boundedPose(message.robotPoseToField) ||
        !boundedValue(message.kickDir) ||
        !boundedValue(message.thetaRb) ||
        !boundedValue(message.assistTarget.x) ||
        !boundedValue(message.assistTarget.y) ||
        !boundedPoint(message.freeKickBall) ||
        !boundedValue(message.freeKickPassTarget.x) ||
        !boundedValue(message.freeKickPassTarget.y) ||
        !validSlot(message.assistSlot) || message.assistPhase < 0 ||
        message.assistPhase > static_cast<int8_t>(AssistPhase::TRANSIT_SLOT)) {
        return false;
    }
    return std::all_of(
               message.assistSlots.begin(), message.assistSlots.end(), validSlot) &&
        std::all_of(
               message.freeKickAssistSlots.begin(),
               message.freeKickAssistSlots.end(), validSlot);
}

} // namespace

static_assert(
    MAX_NUM_PLAYERS == TEAM_COMMUNICATION_MAX_PLAYERS,
    "Team wire protocol player capacity must match the local data model");

BrainCommunication::BrainCommunication(Brain *brain)
    : brain_(brain), bootId_(makeBootId())
{
}

BrainCommunication::~BrainCommunication()
{
    stopTeamCommunication();
    stopGameControllerUnicast();
}

void BrainCommunication::initCommunication()
{
    initGameControllerUnicast();
    if (!brain_->config->enableCom) {
        std::cout << YELLOW_CODE << "Teammate communication disabled."
                  << RESET_CODE << std::endl;
        return;
    }

    std::string keyError;
    const std::string keyHex = brain_->get_parameter(
        "communication.team_secret_hex").as_string();
    if (!team_communication_protocol::parseAuthenticationKey(
            keyHex, authenticationKey_, &keyError)) {
        brain_->config->enableCom = false;
        brain_->log->log(
            "error/communication", rerun::TextLog(keyError));
        std::cerr << keyError << std::endl;
        return;
    }

    stateSendIntervalMs_ = teamCommunicationIntervalMs();
    const double effectiveTimeoutMs = effectiveTacticalTimeoutMs(
        stateSendIntervalMs_);
    const double configuredTimeoutMs = brain_->get_parameter(
        "strategy.cooperation.tactical_packet_timeout_ms").as_double();
    if (!std::isfinite(configuredTimeoutMs) ||
        configuredTimeoutMs < effectiveTimeoutMs) {
        const auto result = brain_->set_parameter(rclcpp::Parameter(
            "strategy.cooperation.tactical_packet_timeout_ms",
            effectiveTimeoutMs));
        if (!result.successful) {
            brain_->config->enableCom = false;
            brain_->log->log(
                "error/communication",
                rerun::TextLog(
                    "Unable to make tactical timeout consistent with the team packet rate: " +
                    result.reason));
            return;
        }
        std::cout << YELLOW_CODE << format(
            "Raised tactical packet timeout to %.0f ms for a %d ms send interval",
            effectiveTimeoutMs, stateSendIntervalMs_) << RESET_CODE << std::endl;
    }

    if (!initTeamCommunication()) {
        brain_->config->enableCom = false;
        std::cout << RED_CODE
                  << "Teammate communication initialization failed; cooperation disabled."
                  << RESET_CODE << std::endl;
        return;
    }
    std::cout << GREEN_CODE << format(
        "Teammate communication v%u enabled: team_channel=%u state_source=%u interval=%dms",
        static_cast<unsigned>(TEAM_COMMUNICATION_PROTOCOL_VERSION),
        static_cast<unsigned>(discoveryPort_),
        static_cast<unsigned>(statePort_), stateSendIntervalMs_)
              << RESET_CODE << std::endl;
}

void BrainCommunication::initGameControllerUnicast()
{
    try {
        gameControllerSocket_ = socket(AF_INET, SOCK_DGRAM, 0);
        if (gameControllerSocket_ < 0) {
            throw std::runtime_error(strerror(errno));
        }
        const std::string ip = brain_->get_parameter(
            "game_control_ip").as_string();
        gameControllerAddress_ = {};
        gameControllerAddress_.sin_family = AF_INET;
        if (inet_pton(
                AF_INET, ip.c_str(), &gameControllerAddress_.sin_addr) != 1 ||
            gameControllerAddress_.sin_addr.s_addr == htonl(INADDR_ANY)) {
            throw std::invalid_argument(
                "game_control_ip must be a valid, non-zero IPv4 address: " + ip);
        }
        gameControllerAddress_.sin_port = htons(GAMECONTROLLER_RETURN_PORT);
        gameControllerRunning_ = true;
        gameControllerThread_ = std::thread(
            [this]() { sendToGameController(); });
        std::cout << GREEN_CODE << format(
            "GameController return enabled: target=%s packet=%zu bytes interval=%dms",
            endpointString(
                gameControllerAddress_.sin_addr.s_addr,
                GAMECONTROLLER_RETURN_PORT).c_str(),
            sizeof(RoboCupGameControlReturnData),
            GAME_CONTROLLER_INTERVAL_MS) << RESET_CODE << std::endl;
    } catch (const std::exception &error) {
        closeSocket(gameControllerSocket_);
        brain_->log->log(
            "error/communication",
            rerun::TextLog(
                std::string("GameController response socket failed: ") +
                error.what()));
    }
}

void BrainCommunication::stopGameControllerUnicast()
{
    gameControllerRunning_ = false;
    wakeCv_.notify_all();
    if (gameControllerThread_.joinable()) gameControllerThread_.join();
    closeSocket(gameControllerSocket_);
}

void BrainCommunication::sendToGameController()
{
    while (gameControllerRunning_) {
        const auto data = brain_->gameControllerReturnSnapshot();
        const ssize_t sent = sendto(
            gameControllerSocket_, &data, sizeof(data), 0,
            reinterpret_cast<sockaddr *>(&gameControllerAddress_),
            sizeof(gameControllerAddress_));
        if (sent != static_cast<ssize_t>(sizeof(data))) {
            char target[INET_ADDRSTRLEN]{};
            inet_ntop(
                AF_INET, &gameControllerAddress_.sin_addr,
                target, sizeof(target));
            std::cout << RED_CODE << format(
                "GameController send failed: target=%s:%u sent=%ld error=%s",
                target,
                static_cast<unsigned>(
                    ntohs(gameControllerAddress_.sin_port)),
                static_cast<long>(sent), strerror(errno))
                      << RESET_CODE << std::endl;
        }
        std::unique_lock<std::mutex> lock(wakeMutex_);
        wakeCv_.wait_for(
            lock, std::chrono::milliseconds(GAME_CONTROLLER_INTERVAL_MS),
            [this]() { return !gameControllerRunning_; });
    }
}

bool BrainCommunication::initTeamCommunication()
{
    try {
        discoveryPort_ = discoveryPortForTeam(brain_->config->teamId);
        statePort_ = statePortForPlayer(
            brain_->config->teamId, brain_->config->playerId);

        discoverySendSocket_ = socket(AF_INET, SOCK_DGRAM, 0);
        if (discoverySendSocket_ < 0) {
            throw std::runtime_error(
                std::string("discovery send socket: ") + strerror(errno));
        }
        int enabled = 1;
        if (setsockopt(
                discoverySendSocket_, SOL_SOCKET, SO_BROADCAST,
                &enabled, sizeof(enabled)) < 0) {
            throw std::runtime_error(
                std::string("SO_BROADCAST: ") + strerror(errno));
        }
        discoveryAddress_ = {};
        discoveryAddress_.sin_family = AF_INET;
        discoveryAddress_.sin_port = htons(discoveryPort_);
        const std::string configuredDiscoveryAddress = brain_->get_parameter(
            "communication.discovery_address").as_string();
        if (configuredDiscoveryAddress == "auto") {
            std::string interfaceName;
            std::string resolutionError;
            const std::string gameControllerIp = brain_->get_parameter(
                "game_control_ip").as_string();
            if (!directedBroadcastForTarget(
                    gameControllerIp, discoveryAddress_.sin_addr,
                    interfaceName, resolutionError)) {
                throw std::runtime_error(
                    "automatic discovery broadcast resolution failed: " +
                    resolutionError +
                    "; set communication.discovery_address explicitly in config_local.yaml");
            }
            std::cout << GREEN_CODE << format(
                "Using directed discovery broadcast %s via %s",
                endpointString(
                    discoveryAddress_.sin_addr.s_addr,
                    discoveryPort_).c_str(),
                interfaceName.c_str()) << RESET_CODE << std::endl;
        } else if (inet_pton(
                       AF_INET, configuredDiscoveryAddress.c_str(),
                       &discoveryAddress_.sin_addr) != 1 ||
                   discoveryAddress_.sin_addr.s_addr == htonl(INADDR_ANY)) {
            throw std::invalid_argument(
                "communication.discovery_address must be 'auto' or a non-zero IPv4 address: " +
                configuredDiscoveryAddress);
        }

        discoveryReceiveSocket_ = socket(AF_INET, SOCK_DGRAM, 0);
        if (discoveryReceiveSocket_ < 0) {
            throw std::runtime_error(
                std::string("discovery receive socket: ") + strerror(errno));
        }
        if (setsockopt(
                discoveryReceiveSocket_, SOL_SOCKET, SO_REUSEADDR,
                &enabled, sizeof(enabled)) < 0) {
            throw std::runtime_error(
                std::string("discovery SO_REUSEADDR: ") + strerror(errno));
        }
        setReceiveTimeout(
            discoveryReceiveSocket_, SOCKET_RECEIVE_TIMEOUT_MS);
        sockaddr_in discoveryBind{};
        discoveryBind.sin_family = AF_INET;
        discoveryBind.sin_addr.s_addr = htonl(INADDR_ANY);
        discoveryBind.sin_port = htons(discoveryPort_);
        if (bind(
                discoveryReceiveSocket_,
                reinterpret_cast<sockaddr *>(&discoveryBind),
                sizeof(discoveryBind)) < 0) {
            throw std::runtime_error(
                std::string("discovery bind: ") + strerror(errno));
        }

        stateSocket_ = socket(AF_INET, SOCK_DGRAM, 0);
        if (stateSocket_ < 0) {
            throw std::runtime_error(
                std::string("state socket: ") + strerror(errno));
        }
        if (setsockopt(
                stateSocket_, SOL_SOCKET, SO_BROADCAST,
                &enabled, sizeof(enabled)) < 0) {
            throw std::runtime_error(
                std::string("state SO_BROADCAST: ") + strerror(errno));
        }
        sockaddr_in stateBind{};
        stateBind.sin_family = AF_INET;
        stateBind.sin_addr.s_addr = htonl(INADDR_ANY);
        stateBind.sin_port = htons(statePort_);
        if (bind(
                stateSocket_, reinterpret_cast<sockaddr *>(&stateBind),
                sizeof(stateBind)) < 0) {
            throw std::runtime_error(
                std::string("state bind: ") + strerror(errno));
        }

        // Fail initialization immediately when the selected broadcast has no
        // usable route. The old implementation stayed enabled and retried an
        // unreachable limited broadcast forever without discovering peers.
        TeamDiscoveryMsg probe;
        probe.bootId = bootId_;
        probe.sequence = discoverySequence_++;
        probe.teamId = static_cast<uint16_t>(brain_->config->teamId);
        probe.playerId = static_cast<uint8_t>(brain_->config->playerId);
        probe.statePort = statePort_;
        const auto probePacket = team_communication_protocol::encodeDiscovery(
            probe, authenticationKey_);
        const ssize_t probeSent = sendto(
            discoverySendSocket_, probePacket.data(), probePacket.size(), 0,
            reinterpret_cast<sockaddr *>(&discoveryAddress_),
            sizeof(discoveryAddress_));
        if (probeSent != static_cast<ssize_t>(probePacket.size())) {
            throw std::runtime_error(format(
                "discovery route probe failed: target=%s sent=%ld expected=%zu error=%s",
                endpointString(
                    discoveryAddress_.sin_addr.s_addr,
                    discoveryPort_).c_str(),
                static_cast<long>(probeSent), probePacket.size(),
                strerror(errno)));
        }

        discoverySendRunning_ = true;
        discoveryReceiveRunning_ = true;
        stateSendRunning_ = true;
        discoverySendThread_ = std::thread([this]() { sendDiscovery(); });
        discoveryReceiveThread_ = std::thread([this]() { receiveDiscovery(); });
        stateSendThread_ = std::thread([this]() { sendState(); });
        return true;
    } catch (const std::exception &error) {
        brain_->log->log(
            "error/communication",
            rerun::TextLog(
                std::string("Teammate communication setup failed: ") +
                error.what()));
        stopTeamCommunication();
        return false;
    }
}

void BrainCommunication::stopTeamCommunication()
{
    discoverySendRunning_ = false;
    discoveryReceiveRunning_ = false;
    stateSendRunning_ = false;
    wakeCv_.notify_all();

    if (discoverySendThread_.joinable()) discoverySendThread_.join();
    if (stateSendThread_.joinable()) stateSendThread_.join();
    if (discoveryReceiveThread_.joinable()) discoveryReceiveThread_.join();

    closeSocket(discoverySendSocket_);
    closeSocket(discoveryReceiveSocket_);
    closeSocket(stateSocket_);
    peerDirectory_.clear();
}

void BrainCommunication::sendDiscovery()
{
    while (discoverySendRunning_) {
        TeamDiscoveryMsg message;
        message.bootId = bootId_;
        message.sequence = discoverySequence_++;
        message.teamId = static_cast<uint16_t>(brain_->config->teamId);
        message.playerId = static_cast<uint8_t>(brain_->config->playerId);
        message.statePort = statePort_;
        const auto packet = team_communication_protocol::encodeDiscovery(
            message, authenticationKey_);
        const ssize_t sent = sendto(
            discoverySendSocket_, packet.data(), packet.size(), 0,
            reinterpret_cast<sockaddr *>(&discoveryAddress_),
            sizeof(discoveryAddress_));
        if (sent != static_cast<ssize_t>(packet.size())) {
            std::cout << RED_CODE << format(
                "Discovery send failed: target=%s sent=%ld expected=%zu error=%s",
                endpointString(
                    discoveryAddress_.sin_addr.s_addr,
                    discoveryPort_).c_str(),
                static_cast<long>(sent), packet.size(), strerror(errno))
                      << RESET_CODE << std::endl;
        }
        std::unique_lock<std::mutex> lock(wakeMutex_);
        wakeCv_.wait_for(
            lock, std::chrono::milliseconds(DISCOVERY_INTERVAL_MS),
            [this]() { return !discoverySendRunning_; });
    }
}

void BrainCommunication::receiveDiscovery()
{
    std::array<uint8_t, team_communication_protocol::MAX_PACKET_BYTES> buffer{};
    while (discoveryReceiveRunning_) {
        sockaddr_in source{};
        socklen_t sourceSize = sizeof(source);
        const ssize_t received = recvfrom(
            discoveryReceiveSocket_, buffer.data(), buffer.size(), 0,
            reinterpret_cast<sockaddr *>(&source), &sourceSize);
        if (received < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR &&
                discoveryReceiveRunning_) {
                std::cout << RED_CODE << format(
                    "Team channel receive failed: %s", strerror(errno))
                          << RESET_CODE << std::endl;
            }
            continue;
        }

        TeamDiscoveryMsg message;
        std::string error;
        if (!team_communication_protocol::decodeDiscovery(
                buffer.data(), static_cast<std::size_t>(received),
                authenticationKey_, message, &error)) {
            processStatePacket(
                buffer.data(), static_cast<std::size_t>(received), source);
            continue;
        }
        if (message.teamId != brain_->config->teamId ||
            message.playerId == 0 ||
            message.playerId > brain_->config->numOfPlayers ||
            message.bootId == 0 ||
            message.statePort != statePortForPlayer(
                message.teamId, message.playerId)) {
            continue;
        }
        if (message.playerId == brain_->config->playerId) {
            if (message.bootId != bootId_) {
                std::cout << RED_CODE << format(
                    "Duplicate player ID %u discovered from %s",
                    static_cast<unsigned>(message.playerId),
                    endpointString(
                        source.sin_addr.s_addr, message.statePort).c_str())
                          << RESET_CODE << std::endl;
            }
            continue;
        }

        const auto result = peerDirectory_.observe(
            message, source.sin_addr.s_addr,
            TeamPeerDirectory::Clock::now());
        if (result == TeamPeerDirectory::DiscoveryResult::Added ||
            result == TeamPeerDirectory::DiscoveryResult::BootChanged) {
            {
                const int teammateIndex = message.playerId - 1;
                std::lock_guard<std::mutex> lock(
                    brain_->data->teamStatusMutex);
                TMStatus &status = brain_->data->tmStatus[teammateIndex];
                if (status.bootId != message.bootId) {
                    status = TMStatus{};
                }
            }
            std::cout << GREEN_CODE << format(
                "Teammate P%u %s at %s",
                static_cast<unsigned>(message.playerId),
                result == TeamPeerDirectory::DiscoveryResult::Added
                    ? "discovered" : "restarted",
                endpointString(
                    source.sin_addr.s_addr, message.statePort).c_str())
                      << RESET_CODE << std::endl;
        }
    }
}

int BrainCommunication::teamCommunicationIntervalMs() const
{
    double rateHz = DEFAULT_TEAM_RATE_HZ;
    brain_->get_parameter("communication.team_broadcast_rate_hz", rateHz);
    if (!std::isfinite(rateHz)) rateHz = DEFAULT_TEAM_RATE_HZ;
    rateHz = std::clamp(rateHz, MIN_TEAM_RATE_HZ, MAX_TEAM_RATE_HZ);
    return std::max(50, static_cast<int>(std::lround(1000.0 / rateHz)));
}

double BrainCommunication::effectiveTacticalTimeoutMs(int intervalMs) const
{
    return std::max(100.0, intervalMs * 3.0 + 100.0);
}

void BrainCommunication::sendState()
{
    while (stateSendRunning_) {
        TeamCommunicationMsg message;
        message.bootId = bootId_;
        message.sequence = stateSequence_++;
        message.teamId = static_cast<uint16_t>(brain_->config->teamId);
        message.playerId = static_cast<uint8_t>(brain_->config->playerId);
        message.playerStartRole = brain_->config->playerRole == "striker" ? 1 : 2;

        TeamOutboundSnapshot snapshot;
        {
            std::lock_guard<std::mutex> lock(brain_->data->teamOutboundMutex);
            snapshot = brain_->data->tmOutboundSnapshot;
        }
        message.playerRole = static_cast<uint8_t>(snapshot.playerRole);
        message.isAlive = snapshot.isAlive;
        message.isLead = snapshot.isLead;
        message.ballDetected = snapshot.ballDetected;
        message.ballLocationKnown = snapshot.ballLocationKnown;
        message.ballConfidence = snapshot.ballConfidence;
        message.ballRange = snapshot.ballRange;
        message.cost = snapshot.cost;
        message.ballPosToField = {
            snapshot.ballPosToField.x,
            snapshot.ballPosToField.y,
            snapshot.ballPosToField.z,
        };
        message.robotPoseToField = {
            snapshot.robotPoseToField.x,
            snapshot.robotPoseToField.y,
            snapshot.robotPoseToField.theta,
        };
        message.kickDir = snapshot.kickDir;
        message.thetaRb = snapshot.thetaRb;
        message.ballOwnerId = static_cast<uint8_t>(snapshot.ballOwnerId);
        message.leaderTerm = snapshot.leaderTerm;
        message.formationOwnerId = static_cast<uint8_t>(snapshot.formationOwnerId);
        message.formationRevision = snapshot.formationRevision;
        message.formationShadowSide = static_cast<int8_t>(
            snapshot.formationShadowSide);
        for (int i = 0; i < MAX_NUM_PLAYERS; ++i) {
            message.assistSlots[i] = static_cast<int8_t>(snapshot.assistSlots[i]);
            message.freeKickAssistSlots[i] = static_cast<int8_t>(
                snapshot.freeKickAssistSlots[i]);
        }
        message.assistSlot = static_cast<int8_t>(snapshot.assistSlot);
        message.assistPhase = static_cast<int8_t>(snapshot.assistPhase);
        message.assistTarget = {
            snapshot.assistTarget.x,
            snapshot.assistTarget.y,
        };
        message.kickoffActive = snapshot.kickoffActive;
        message.kickoffOwnerId = static_cast<uint8_t>(snapshot.kickoffOwnerId);
        message.kickoffOwnerTerm = snapshot.kickoffOwnerTerm;
        message.freeKickProposerBootId = snapshot.freeKickProposerBootId;
        message.freeKickRefereePacketNumber =
            snapshot.freeKickRefereePacketNumber;
        message.freeKickEventId = snapshot.freeKickEventId;
        message.freeKickLeaderTerm = snapshot.freeKickLeaderTerm;
        message.freeKickType = static_cast<uint8_t>(snapshot.freeKickType);
        message.freeKickPhase = static_cast<uint8_t>(snapshot.freeKickPhase);
        message.freeKickOurRestart = snapshot.freeKickOurRestart;
        message.freeKickKickerId = static_cast<uint8_t>(snapshot.freeKickKickerId);
        message.freeKickReceiverId = static_cast<uint8_t>(
            snapshot.freeKickReceiverId);
        message.freeKickBall = {
            snapshot.freeKickBall.x,
            snapshot.freeKickBall.y,
            snapshot.freeKickBall.z,
        };
        message.freeKickPassTarget = {
            snapshot.freeKickPassTarget.x,
            snapshot.freeKickPassTarget.y,
        };
        message.freeKickExecutionElapsedMs =
            snapshot.freeKickExecutionElapsedMs;

        const auto packet = team_communication_protocol::encodeState(
            message, authenticationKey_);
        const ssize_t sent = sendto(
            stateSocket_, packet.data(), packet.size(), 0,
            reinterpret_cast<sockaddr *>(&discoveryAddress_),
            sizeof(discoveryAddress_));
        if (sent != static_cast<ssize_t>(packet.size())) {
            std::cout << RED_CODE << format(
                "Team state broadcast failed: target=%s sent=%ld expected=%zu error=%s",
                endpointString(
                    discoveryAddress_.sin_addr.s_addr,
                    discoveryPort_).c_str(),
                static_cast<long>(sent), packet.size(), strerror(errno))
                      << RESET_CODE << std::endl;
        }

        std::unique_lock<std::mutex> lock(wakeMutex_);
        wakeCv_.wait_for(
            lock, std::chrono::milliseconds(stateSendIntervalMs_),
            [this]() { return !stateSendRunning_; });
    }
}

void BrainCommunication::processStatePacket(
    const uint8_t *packetData,
    std::size_t packetSize,
    const sockaddr_in &source)
{
    auto debugLog = [this](const std::string &message) {
        if (!brain_->log->shouldLog(
                "communication_receive_debug",
                brain_->config->rerunLogDebugHz)) return;
        brain_->log->setTimeNow();
        brain_->log->log("debug/receiveMsg", rerun::TextLog(message));
    };

    TeamCommunicationMsg message;
    std::string error;
    if (!team_communication_protocol::decodeState(
            packetData, packetSize, authenticationKey_, message, &error)) {
        return;
    }
    if (message.teamId != brain_->config->teamId ||
        message.playerId == brain_->config->playerId ||
        !stateSemanticallyValid(message, brain_->config->numOfPlayers)) {
        return;
    }
    if (!peerDirectory_.acceptNewStateSource(
            message.playerId, message.bootId, message.sequence,
            source.sin_addr.s_addr, ntohs(source.sin_port),
            TeamPeerDirectory::Clock::now())) {
        debugLog(format(
            "Rejected stale state or state without matching discovery: player=%u source=%s",
            static_cast<unsigned>(message.playerId),
            endpointString(
                source.sin_addr.s_addr, ntohs(source.sin_port)).c_str()));
        return;
    }

    const int teammateIndex = message.playerId - 1;
    std::unique_lock<std::mutex> lock(brain_->data->teamStatusMutex);
    TMStatus &status = brain_->data->tmStatus[teammateIndex];
    if (status.bootId != message.bootId) {
        status = TMStatus{};
    }

    status.role = message.playerRole == 1 ? "striker" : "goal_keeper";
    status.startRole = message.playerStartRole == 1
        ? "striker" : "goal_keeper";
    status.isAlive = message.isAlive;
    status.ballDetected = message.ballDetected;
    status.ballLocationKnown = message.ballLocationKnown;
    status.ballConfidence = message.ballConfidence;
    status.ballRange = message.ballRange;
    status.cost = message.cost;
    status.isLead = message.isLead;
    status.ballPosToField = {
        message.ballPosToField.x,
        message.ballPosToField.y,
        message.ballPosToField.z,
    };
    status.robotPoseToField = {
        message.robotPoseToField.x,
        message.robotPoseToField.y,
        message.robotPoseToField.theta,
    };
    status.kickDir = message.kickDir;
    status.thetaRb = message.thetaRb;
    status.bootId = message.bootId;
    status.sequence = message.sequence;
    status.ballOwnerId = message.ballOwnerId;
    status.leaderTerm = message.leaderTerm;
    status.formationOwnerId = message.formationOwnerId;
    status.formationRevision = message.formationRevision;
    status.formationShadowSide = message.formationShadowSide;
    for (int i = 0; i < MAX_NUM_PLAYERS; ++i) {
        status.assistSlots[i] = message.assistSlots[i];
        status.freeKickAssistSlots[i] = message.freeKickAssistSlots[i];
    }
    status.assistSlot = static_cast<AssistSlot>(message.assistSlot);
    status.assistPhase = static_cast<AssistPhase>(message.assistPhase);
    status.assistTarget = {
        message.assistTarget.x,
        message.assistTarget.y,
    };
    status.kickoffActive = message.kickoffActive;
    status.kickoffOwnerId = message.kickoffOwnerId;
    status.kickoffOwnerTerm = message.kickoffOwnerTerm;
    status.freeKickProposerBootId = message.freeKickProposerBootId;
    status.freeKickRefereePacketNumber =
        message.freeKickRefereePacketNumber;
    status.freeKickEventId = message.freeKickEventId;
    status.freeKickLeaderTerm = message.freeKickLeaderTerm;
    status.freeKickType = message.freeKickType;
    status.freeKickPhase = message.freeKickPhase;
    status.freeKickOurRestart = message.freeKickOurRestart;
    status.freeKickKickerId = message.freeKickKickerId;
    status.freeKickReceiverId = message.freeKickReceiverId;
    status.freeKickBall = {
        message.freeKickBall.x,
        message.freeKickBall.y,
        message.freeKickBall.z,
    };
    status.freeKickPassTarget = {
        message.freeKickPassTarget.x,
        message.freeKickPassTarget.y,
    };
    status.freeKickExecutionElapsedMs =
        message.freeKickExecutionElapsedMs;
    status.steadyTimeLastCom = std::chrono::steady_clock::now();
    lock.unlock();

    debugLog(format(
        "P%u state accepted: sequence=%u alive=%d lead=%d cost=%.2f",
        static_cast<unsigned>(message.playerId),
        static_cast<unsigned>(message.sequence),
        message.isAlive, message.isLead, message.cost));
}

uint16_t BrainCommunication::discoveryPortForTeam(int teamId)
{
    // GameController listens for each team's arbitrary-format messages here.
    return static_cast<uint16_t>(10000 + teamId);
}

uint16_t BrainCommunication::statePortForPlayer(int teamId, int playerId)
{
    return static_cast<uint16_t>(
        30000 + teamId * MAX_NUM_PLAYERS + playerId);
}
