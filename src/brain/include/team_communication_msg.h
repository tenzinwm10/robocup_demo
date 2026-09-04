#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

constexpr uint16_t TEAM_COMMUNICATION_PROTOCOL_VERSION = 6;
constexpr std::size_t TEAM_COMMUNICATION_MAX_PLAYERS = 20;

struct TeamPoint3
{
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

struct TeamPoint2
{
    double x = 0.0;
    double y = 0.0;
};

struct TeamPose2
{
    double x = 0.0;
    double y = 0.0;
    double theta = 0.0;
};

// Logical teammate state. The wire representation is defined explicitly in
// team_communication_protocol.cpp and must never depend on this struct's ABI.
struct TeamCommunicationMsg
{
    uint64_t bootId = 0;
    uint32_t sequence = 0;
    uint16_t teamId = 0;
    uint8_t playerId = 0;
    uint8_t playerRole = 0; // 1: striker, 2: goalkeeper
    uint8_t playerStartRole = 0;
    bool isAlive = false;
    bool isLead = false;
    bool ballDetected = false;
    bool ballLocationKnown = false;
    double ballConfidence = 0.0;
    double ballRange = 0.0;
    double cost = 0.0;
    TeamPoint3 ballPosToField;
    TeamPose2 robotPoseToField;
    double kickDir = 0.0;
    double thetaRb = 0.0;
    uint8_t ballOwnerId = 0;
    uint32_t leaderTerm = 0;
    uint8_t formationOwnerId = 0;
    uint32_t formationRevision = 0;
    int8_t formationShadowSide = 0;
    std::array<int8_t, TEAM_COMMUNICATION_MAX_PLAYERS> assistSlots{};
    int8_t assistSlot = 0;
    int8_t assistPhase = 0;
    TeamPoint2 assistTarget;
    bool kickoffActive = false;
    uint8_t kickoffOwnerId = 0;
    uint32_t kickoffOwnerTerm = 0;
    uint64_t freeKickProposerBootId = 0;
    uint32_t freeKickRefereePacketNumber = 0;
    uint32_t freeKickEventId = 0;
    uint32_t freeKickLeaderTerm = 0;
    uint8_t freeKickType = 0;
    uint8_t freeKickPhase = 0;
    bool freeKickOurRestart = false;
    uint8_t freeKickKickerId = 0;
    uint8_t freeKickReceiverId = 0;
    TeamPoint3 freeKickBall;
    TeamPoint2 freeKickPassTarget;
    std::array<int8_t, TEAM_COMMUNICATION_MAX_PLAYERS> freeKickAssistSlots{};
    uint32_t freeKickExecutionElapsedMs = 0;
};

struct TeamDiscoveryMsg
{
    uint64_t bootId = 0;
    uint32_t sequence = 0;
    uint16_t teamId = 0;
    uint8_t playerId = 0;
    uint16_t statePort = 0;
};
