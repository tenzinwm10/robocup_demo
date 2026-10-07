// Observation only: use the exact branch decoder, including CRC and SipHash.
#include <cstring>
#include <string>
#include "team_communication_protocol.h"
#include "booster_interface/third_party/nlohmann_json/json.hpp"
using nlohmann::json;
extern "C" int inspect_team(const unsigned char* data, size_t size, const char* secret,
                            char* output, size_t capacity) {
    namespace protocol = team_communication_protocol;
    protocol::AuthenticationKey key;
    std::string error;
    json result;
    if (!protocol::parseAuthenticationKey(secret, key, &error)) {
        result = {{"valid", false}, {"error", "observer authentication key unavailable"}};
    } else if (size >= 4 && std::memcmp(data, "TD2D", 4) == 0) {
        TeamDiscoveryMsg message;
        bool valid = protocol::decodeDiscovery(data, size, key, message, &error);
        result = {{"valid", valid}, {"kind", "discovery"}, {"error", error}};
        if (valid) result.update({{"team", message.teamId}, {"player", message.playerId},
            {"boot_id", std::to_string(message.bootId)}, {"sequence", message.sequence},
            {"state_source_port", message.statePort}});
    } else {
        TeamCommunicationMsg message;
        bool valid = protocol::decodeState(data, size, key, message, &error);
        result = {{"valid", valid}, {"kind", "state"}, {"error", error}};
        if (valid) result.update({{"team", message.teamId}, {"player", message.playerId},
            {"boot_id", std::to_string(message.bootId)}, {"sequence", message.sequence},
            {"role", message.playerRole}, {"alive", message.isAlive}, {"lead", message.isLead},
            {"ball_detected", message.ballDetected}, {"ball_known", message.ballLocationKnown},
            {"ball_confidence", message.ballConfidence}, {"cost", message.cost},
            {"ball_owner", message.ballOwnerId}, {"leader_term", message.leaderTerm},
            {"formation_owner", message.formationOwnerId}, {"formation_revision", message.formationRevision},
            {"robot_pose", {message.robotPoseToField.x, message.robotPoseToField.y, message.robotPoseToField.theta}},
            {"ball_position", {message.ballPosToField.x, message.ballPosToField.y, message.ballPosToField.z}},
            {"kickoff_active", message.kickoffActive}, {"kickoff_owner", message.kickoffOwnerId},
            {"free_kick_type", message.freeKickType}, {"free_kick_phase", message.freeKickPhase},
            {"free_kick_kicker", message.freeKickKickerId}, {"free_kick_receiver", message.freeKickReceiverId}});
    }
    std::string text = result.dump();
    if (text.size()+1 > capacity) return -1;
    std::memcpy(output, text.c_str(), text.size()+1);
    return 0;
}
