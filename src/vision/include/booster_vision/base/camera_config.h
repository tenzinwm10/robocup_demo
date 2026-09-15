#pragma once

#include <string>

#include <yaml-cpp/yaml.h>

namespace booster_vision {

// The on-robot calibration file may only record the camera type and calibration
// parameters. Realsense and D-Robotics cameras are both exposed through the
// unified Booster camera topics. Preserve explicit topics from older or
// simulated configurations, and use the unified backend names when omitted.
inline std::string GetCameraTopic(const YAML::Node &camera, const std::string &key) {
    const YAML::Node topic_node = camera[key];
    if (topic_node && topic_node.IsScalar()) {
        try {
            const std::string topic = topic_node.as<std::string>();
            if (!topic.empty()) {
                return topic;
            }
        } catch (const YAML::Exception &) {
            return "";
        }
    }

    if (key == "color_topic") {
        return "/boostercamera/head/rgb";
    }
    if (key == "depth_topic") {
        return "/boostercamera/head/depth";
    }
    if (key == "intrin_topic") {
        return "/boostercamera/head/rgb/camera_info";
    }
    return "";
}

}  // namespace booster_vision
