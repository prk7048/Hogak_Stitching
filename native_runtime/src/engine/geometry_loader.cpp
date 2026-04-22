#include "engine/geometry_loader.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/core.hpp>

namespace hogak::engine {
std::string sanitize_numeric_text(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    for (const char ch : text) {
        if (std::isdigit(static_cast<unsigned char>(ch)) || ch == '-' || ch == '+' || ch == '.' || ch == 'e' || ch == 'E') {
            out.push_back(ch);
        } else {
            out.push_back(' ');
        }
    }
    return out;
}

bool parse_homography_numbers(const std::string& text, cv::Mat* homography_out) {
    if (homography_out == nullptr) {
        return false;
    }
    std::istringstream values(sanitize_numeric_text(text));
    std::array<double, 9> data{};
    for (double& value : data) {
        if (!(values >> value)) {
            return false;
        }
    }
    *homography_out = cv::Mat(3, 3, CV_64F, data.data()).clone();
    return true;
}

bool extract_json_array_for_key(const std::string& text, const std::string& key, std::string* array_text_out) {
    if (array_text_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto first_bracket = text.find('[', key_pos);
    if (first_bracket == std::string::npos) {
        return false;
    }

    int depth = 0;
    for (std::size_t index = first_bracket; index < text.size(); ++index) {
        const char ch = text[index];
        if (ch == '[') {
            depth += 1;
        } else if (ch == ']') {
            depth -= 1;
            if (depth == 0) {
                *array_text_out = text.substr(first_bracket, index - first_bracket + 1);
                return true;
            }
        }
    }
    return false;
}

bool extract_json_object_for_key(const std::string& text, const std::string& key, std::string* object_text_out) {
    if (object_text_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto first_brace = text.find('{', key_pos);
    if (first_brace == std::string::npos) {
        return false;
    }

    int depth = 0;
    for (std::size_t index = first_brace; index < text.size(); ++index) {
        const char ch = text[index];
        if (ch == '{') {
            depth += 1;
        } else if (ch == '}') {
            depth -= 1;
            if (depth == 0) {
                *object_text_out = text.substr(first_brace, index - first_brace + 1);
                return true;
            }
        }
    }
    return false;
}

bool extract_json_string_for_key(const std::string& text, const std::string& key, std::string* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto colon_pos = text.find(':', key_pos + key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    const auto quote_pos = text.find('"', colon_pos + 1);
    if (quote_pos == std::string::npos) {
        return false;
    }
    const auto end_quote_pos = text.find('"', quote_pos + 1);
    if (end_quote_pos == std::string::npos || end_quote_pos <= quote_pos) {
        return false;
    }
    *value_out = text.substr(quote_pos + 1, end_quote_pos - quote_pos - 1);
    return true;
}

bool extract_json_bool(const std::string& text, const std::string& key, bool* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto colon_pos = text.find(':', key_pos + key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    std::size_t value_begin = colon_pos + 1;
    while (value_begin < text.size() && std::isspace(static_cast<unsigned char>(text[value_begin]))) {
        value_begin += 1;
    }
    if (text.compare(value_begin, 4, "true") == 0) {
        *value_out = true;
        return true;
    }
    if (text.compare(value_begin, 5, "false") == 0) {
        *value_out = false;
        return true;
    }
    return false;
}

bool extract_json_number_for_key(const std::string& text, const std::string& key, double* value_out) {
    if (value_out == nullptr) {
        return false;
    }
    const auto key_pos = text.find(key);
    if (key_pos == std::string::npos) {
        return false;
    }
    const auto colon_pos = text.find(':', key_pos + key.size());
    if (colon_pos == std::string::npos) {
        return false;
    }
    std::size_t value_begin = colon_pos + 1;
    while (value_begin < text.size() && std::isspace(static_cast<unsigned char>(text[value_begin]))) {
        value_begin += 1;
    }
    std::size_t value_end = value_begin;
    while (value_end < text.size()) {
        const char ch = text[value_end];
        if (!(std::isdigit(static_cast<unsigned char>(ch)) || ch == '-' || ch == '+' || ch == '.' || ch == 'e' || ch == 'E')) {
            break;
        }
        value_end += 1;
    }
    if (value_end <= value_begin) {
        return false;
    }
    try {
        *value_out = std::stod(text.substr(value_begin, value_end - value_begin));
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

bool parse_numeric_vector(const std::string& text, std::vector<double>* out) {
    if (out == nullptr) {
        return false;
    }
    out->clear();
    std::istringstream values(sanitize_numeric_text(text));
    double value = 0.0;
    while (values >> value) {
        out->push_back(value);
    }
    return !out->empty();
}

std::string runtime_geometry_artifact_candidate_path(const EngineConfig& config) {
    if (!config.geometry.artifact_file.empty()) {
        return config.geometry.artifact_file;
    }
    return {};
}

bool load_runtime_geometry_artifact_from_file(const std::string& path, RuntimeGeometryArtifactData* artifact_out) {
    if (artifact_out == nullptr) {
        return false;
    }
    *artifact_out = RuntimeGeometryArtifactData{};
    artifact_out->artifact_path = path;
    struct ProjectionFieldPresence {
        bool focal = false;
        bool center = false;
        bool virtual_focal = false;
        bool virtual_center = false;
    };
    ProjectionFieldPresence left_projection_presence{};
    ProjectionFieldPresence right_projection_presence{};
    bool crop_enabled_present = false;
    bool crop_rect_present = false;

    auto projection_reference_size = [&](const cv::Size& input_size) {
        return (input_size.width > 0 && input_size.height > 0)
            ? input_size
            : artifact_out->output_size;
    };
    auto projection_center_out_of_bounds = [&](double center_x, double center_y, const cv::Size& input_size) {
        const cv::Size reference_size = projection_reference_size(input_size);
        const int reference_width = std::max(1, reference_size.width);
        const int reference_height = std::max(1, reference_size.height);
        return center_x <= 0.0 ||
               center_x >= static_cast<double>(reference_width) ||
               center_y <= 0.0 ||
               center_y >= static_cast<double>(reference_height);
    };
    auto validate_projection_side_if_present = [&](const ProjectionFieldPresence& presence,
                                                   double focal_px,
                                                   double center_x,
                                                   double center_y,
                                                   double virtual_focal_px,
                                                   double virtual_center_x,
                                                   double virtual_center_y,
                                                   const cv::Size& input_size,
                                                   const cv::Size& virtual_size) {
        if (presence.focal && focal_px <= 0.0) {
            return false;
        }
        if (presence.center && projection_center_out_of_bounds(center_x, center_y, input_size)) {
            return false;
        }
        if (presence.virtual_focal && virtual_focal_px <= 0.0) {
            return false;
        }
        const cv::Size virtual_reference_size =
            (virtual_size.width > 0 && virtual_size.height > 0) ? virtual_size : input_size;
        if (presence.virtual_center &&
            projection_center_out_of_bounds(virtual_center_x, virtual_center_y, virtual_reference_size)) {
            return false;
        }
        return true;
    };
    auto normalized_crop_rect = [&]() {
        const int canvas_width = std::max(0, artifact_out->output_size.width);
        const int canvas_height = std::max(0, artifact_out->output_size.height);
        int x = std::max(0, artifact_out->crop_rect.x);
        int y = std::max(0, artifact_out->crop_rect.y);
        int width = std::max(0, artifact_out->crop_rect.width);
        int height = std::max(0, artifact_out->crop_rect.height);
        if (canvas_width > 0) {
            x = std::min(x, canvas_width);
            width = std::min(width, std::max(0, canvas_width - x));
        }
        if (canvas_height > 0) {
            y = std::min(y, canvas_height);
            height = std::min(height, std::max(0, canvas_height - y));
        }
        return cv::Rect(x, y, width, height);
    };
    auto validate_rectilinear_operator_geometry_or_fail = [&]() {
        if (artifact_out->model != "virtual-center-rectilinear" &&
            artifact_out->model != "virtual_center_rectilinear") {
            return true;
        }
        if (!validate_projection_side_if_present(
                left_projection_presence,
                artifact_out->left_focal_px,
                artifact_out->left_center_x,
                artifact_out->left_center_y,
                artifact_out->left_virtual_focal_px,
                artifact_out->left_virtual_center_x,
                artifact_out->left_virtual_center_y,
                artifact_out->left_input_size,
                artifact_out->output_size)) {
            return false;
        }
        if (!validate_projection_side_if_present(
                right_projection_presence,
                artifact_out->right_focal_px,
                artifact_out->right_center_x,
                artifact_out->right_center_y,
                artifact_out->right_virtual_focal_px,
                artifact_out->right_virtual_center_x,
                artifact_out->right_virtual_center_y,
                artifact_out->right_input_size,
                artifact_out->output_size)) {
            return false;
        }
        if (artifact_out->crop_enabled || crop_enabled_present || crop_rect_present) {
            if (!artifact_out->crop_enabled) {
                return false;
            }
            const cv::Rect normalized_rect = normalized_crop_rect();
            if (normalized_rect.width <= 0 || normalized_rect.height <= 0) {
                return false;
            }
        }
        return true;
    };

    auto sanitize_projection_side = [&](double* focal_px,
                                        double* center_x,
                                        double* center_y,
                                        const cv::Size& input_size) {
        if (focal_px == nullptr || center_x == nullptr || center_y == nullptr) {
            return;
        }
        const cv::Size reference_size =
            (input_size.width > 0 && input_size.height > 0)
                ? input_size
                : artifact_out->output_size;
        const int reference_width = std::max(1, reference_size.width);
        const int reference_height = std::max(1, reference_size.height);
        const double reference_max_dim =
            static_cast<double>(std::max(reference_width, reference_height));
        const bool center_out_of_bounds =
            *center_x <= 0.0 ||
            *center_x >= static_cast<double>(reference_width) ||
            *center_y <= 0.0 ||
            *center_y >= static_cast<double>(reference_height);
        const bool center_matches_output_canvas =
            artifact_out->output_size.width > 0 &&
            artifact_out->output_size.height > 0 &&
            (reference_width != artifact_out->output_size.width ||
             reference_height != artifact_out->output_size.height) &&
            std::abs(*center_x - static_cast<double>(artifact_out->output_size.width) * 0.5) <= 1.0 &&
            std::abs(*center_y - static_cast<double>(artifact_out->output_size.height) * 0.5) <= 1.0;
        const bool should_reset_center = center_out_of_bounds || center_matches_output_canvas;
        const double output_default_focal_px =
            static_cast<double>(std::max(artifact_out->output_size.width, artifact_out->output_size.height)) * 0.90;
        const bool focal_matches_output_canvas =
            should_reset_center &&
            artifact_out->output_size.width > 0 &&
            artifact_out->output_size.height > 0 &&
            std::abs(*focal_px - output_default_focal_px) <= 1.0;
        if (*focal_px <= 0.0 ||
            focal_matches_output_canvas ||
            (center_out_of_bounds && *focal_px > reference_max_dim * 1.25)) {
            *focal_px = reference_max_dim * 0.90;
        }
        if (should_reset_center || *center_x <= 0.0 || *center_x >= static_cast<double>(reference_width)) {
            *center_x = static_cast<double>(reference_width) * 0.5;
        }
        if (should_reset_center || *center_y <= 0.0 || *center_y >= static_cast<double>(reference_height)) {
            *center_y = static_cast<double>(reference_height) * 0.5;
        }
    };
    auto sanitize_virtual_projection_side = [&](double* source_focal_px,
                                                double* source_center_x,
                                                double* source_center_y,
                                                double* virtual_focal_px,
                                                double* virtual_center_x,
                                                double* virtual_center_y,
                                                const cv::Size& input_size,
                                                const cv::Size& virtual_size) {
        sanitize_projection_side(source_focal_px, source_center_x, source_center_y, input_size);
        if (virtual_focal_px == nullptr || virtual_center_x == nullptr || virtual_center_y == nullptr) {
            return;
        }
        if (*virtual_focal_px <= 0.0) {
            *virtual_focal_px = *source_focal_px;
        }
        const cv::Size virtual_reference_size =
            (virtual_size.width > 0 && virtual_size.height > 0) ? virtual_size : input_size;
        if (*virtual_center_x <= 0.0 ||
            *virtual_center_x >= static_cast<double>(std::max(1, virtual_reference_size.width))) {
            *virtual_center_x = static_cast<double>(std::max(1, virtual_reference_size.width)) * 0.5;
        }
        if (*virtual_center_y <= 0.0 ||
            *virtual_center_y >= static_cast<double>(std::max(1, virtual_reference_size.height))) {
            *virtual_center_y = static_cast<double>(std::max(1, virtual_reference_size.height)) * 0.5;
        }
    };
    auto sanitize_crop_rect = [&]() {
        if (!artifact_out->crop_enabled) {
            artifact_out->crop_rect = cv::Rect();
            return;
        }
        const int canvas_width = std::max(0, artifact_out->output_size.width);
        const int canvas_height = std::max(0, artifact_out->output_size.height);
        int x = std::max(0, artifact_out->crop_rect.x);
        int y = std::max(0, artifact_out->crop_rect.y);
        int width = std::max(0, artifact_out->crop_rect.width);
        int height = std::max(0, artifact_out->crop_rect.height);
        if (canvas_width > 0) {
            x = std::min(x, canvas_width);
            width = std::min(width, std::max(0, canvas_width - x));
        }
        if (canvas_height > 0) {
            y = std::min(y, canvas_height);
            height = std::min(height, std::max(0, canvas_height - y));
        }
        artifact_out->crop_rect = cv::Rect(x, y, width, height);
        if (width <= 0 || height <= 0) {
            artifact_out->crop_enabled = false;
            artifact_out->crop_rect = cv::Rect();
        }
    };
    auto normalize_projection_model = [](std::string model) {
        std::transform(model.begin(), model.end(), model.begin(), [](unsigned char ch) {
            return static_cast<char>(std::tolower(ch));
        });
        if (model == "virtual_center_rectilinear" ||
            model == "virtual-center-rectilinear" ||
            model == "rectilinear" ||
            model.empty()) {
            return std::string("rectilinear");
        }
        return model;
    };
    auto normalize_runtime_geometry_model = [](std::string model) {
        std::transform(model.begin(), model.end(), model.begin(), [](unsigned char ch) {
            return static_cast<char>(std::tolower(ch));
        });
        if (model == "virtual_center_rectilinear" ||
            model == "virtual-center-rectilinear" ||
            model == "virtual-center-rectilinear-rigid" ||
            model == "virtual-center-rectilinear-mesh" ||
            model.empty()) {
            return std::string("virtual-center-rectilinear");
        }
        return model;
    };

    try {
        cv::FileStorage fs(path, cv::FileStorage::READ | cv::FileStorage::FORMAT_AUTO);
        if (!fs.isOpened()) {
            return false;
        }

        auto read_size = [](const cv::FileNode& node, cv::Size* size_out) {
            if (size_out == nullptr || node.empty() || !node.isSeq()) {
                return false;
            }
            std::vector<double> values;
            node >> values;
            if (values.size() < 2) {
                return false;
            }
            *size_out = cv::Size(static_cast<int>(std::llround(values[0])), static_cast<int>(std::llround(values[1])));
            return size_out->width > 0 && size_out->height > 0;
        };
        auto read_numeric_sequence = [](const cv::FileNode& node, std::vector<double>* values_out) {
            if (values_out == nullptr || node.empty()) {
                return false;
            }
            values_out->clear();
            const auto append_numeric_values = [&](const cv::FileNode& current,
                                                   std::vector<double>* target,
                                                   const auto& self) -> bool {
                if (target == nullptr || current.empty()) {
                    return false;
                }
                if (current.isSeq()) {
                    for (const auto& child : current) {
                        if (!self(child, target, self)) {
                            return false;
                        }
                    }
                    return !target->empty();
                }
                double numeric_value = 0.0;
                current >> numeric_value;
                target->push_back(numeric_value);
                return true;
            };
            return append_numeric_values(node, values_out, append_numeric_values) && !values_out->empty();
        };
        auto read_matrix_3x3 = [&](const cv::FileNode& node, cv::Mat* matrix_out) {
            if (matrix_out == nullptr || node.empty()) {
                return false;
            }
            std::vector<double> values;
            if (!read_numeric_sequence(node, &values) || values.size() < 9) {
                return false;
            }
            *matrix_out = cv::Mat(3, 3, CV_64F, values.data()).clone();
            return true;
        };
        auto read_float_grid = [](const cv::FileNode& node, cv::Mat* grid_out) {
            if (grid_out == nullptr || node.empty() || !node.isSeq()) {
                return false;
            }
            const int rows = static_cast<int>(node.size());
            if (rows <= 0) {
                return false;
            }
            int cols = -1;
            std::vector<float> values;
            values.reserve(static_cast<std::size_t>(rows) * 16U);
            for (const auto& row_node : node) {
                if (!row_node.isSeq()) {
                    return false;
                }
                const int row_cols = static_cast<int>(row_node.size());
                if (row_cols <= 0) {
                    return false;
                }
                if (cols < 0) {
                    cols = row_cols;
                } else if (cols != row_cols) {
                    return false;
                }
                for (const auto& cell_node : row_node) {
                    double numeric_value = 0.0;
                    cell_node >> numeric_value;
                    values.push_back(static_cast<float>(numeric_value));
                }
            }
            if (cols <= 0 || static_cast<int>(values.size()) != rows * cols) {
                return false;
            }
            *grid_out = cv::Mat(rows, cols, CV_32F, values.data()).clone();
            return true;
        };

        cv::FileNode geometry_node = fs["geometry"];
        if (!geometry_node.empty()) {
            geometry_node["model"] >> artifact_out->model;
            geometry_node["warp_model"] >> artifact_out->alignment_model;
            geometry_node["residual_model"] >> artifact_out->residual_model;
            cv::FileNode homography_node = geometry_node["homography"];
            if (!homography_node.empty()) {
                std::vector<double> values;
                if (read_numeric_sequence(homography_node, &values) && values.size() >= 9) {
                    artifact_out->alignment_matrix =
                        cv::Mat(3, 3, CV_64F, values.data()).clone();
                }
            }
            std::vector<double> output_resolution;
            read_numeric_sequence(geometry_node["output_resolution"], &output_resolution);
            if (output_resolution.size() >= 2) {
                artifact_out->output_size = cv::Size(
                    static_cast<int>(std::llround(output_resolution[0])),
                    static_cast<int>(std::llround(output_resolution[1])));
            }
        }

        cv::FileNode alignment_node = fs["alignment"];
        if (!alignment_node.empty()) {
            alignment_node["model"] >> artifact_out->alignment_model;
            cv::FileNode matrix_node = alignment_node["matrix"];
            if (!matrix_node.empty()) {
                std::vector<double> values;
                if (read_numeric_sequence(matrix_node, &values) && values.size() >= 9) {
                    artifact_out->alignment_matrix =
                        cv::Mat(3, 3, CV_64F, values.data()).clone();
                } else if (values.size() >= 6) {
                    artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
                    artifact_out->alignment_matrix.at<double>(0, 0) = values[0];
                    artifact_out->alignment_matrix.at<double>(0, 1) = values[1];
                    artifact_out->alignment_matrix.at<double>(0, 2) = values[2];
                    artifact_out->alignment_matrix.at<double>(1, 0) = values[3];
                    artifact_out->alignment_matrix.at<double>(1, 1) = values[4];
                    artifact_out->alignment_matrix.at<double>(1, 2) = values[5];
                }
            }
        }
        cv::FileNode mesh_node = fs["mesh"];
        if (!mesh_node.empty()) {
            mesh_node["grid_cols"] >> artifact_out->mesh_grid_cols;
            mesh_node["grid_rows"] >> artifact_out->mesh_grid_rows;
            mesh_node["fallback_used"] >> artifact_out->mesh_fallback_used;
            mesh_node["max_displacement_px"] >> artifact_out->mesh_max_displacement_px;
            mesh_node["max_local_scale_drift"] >> artifact_out->mesh_max_local_scale_drift;
            mesh_node["max_local_rotation_drift"] >> artifact_out->mesh_max_local_rotation_drift;
            read_float_grid(mesh_node["control_displacement_x"], &artifact_out->mesh_control_displacement_x);
            read_float_grid(mesh_node["control_displacement_y"], &artifact_out->mesh_control_displacement_y);
        }

        cv::FileNode projection_node = fs["projection"];
        if (!projection_node.empty()) {
            cv::FileNode left_projection = projection_node["left"];
            cv::FileNode right_projection = projection_node["right"];
            std::vector<double> left_center;
            std::vector<double> right_center;
            std::vector<double> left_virtual_center;
            std::vector<double> right_virtual_center;
            std::vector<double> left_input_resolution;
            std::vector<double> right_input_resolution;
            std::vector<double> left_output_resolution;
            std::vector<double> right_output_resolution;
            if (!left_projection.empty()) {
                left_projection_presence.focal = !left_projection["focal_px"].empty();
                left_projection_presence.center = !left_projection["center"].empty();
                left_projection_presence.virtual_focal = !left_projection["virtual_focal_px"].empty();
                left_projection_presence.virtual_center = !left_projection["virtual_center"].empty();
                left_projection["model"] >> artifact_out->left_projection_model;
                left_projection["focal_px"] >> artifact_out->left_focal_px;
                read_numeric_sequence(left_projection["center"], &left_center);
                left_projection["virtual_focal_px"] >> artifact_out->left_virtual_focal_px;
                read_numeric_sequence(left_projection["virtual_center"], &left_virtual_center);
                read_matrix_3x3(left_projection["virtual_to_source_rotation"], &artifact_out->left_virtual_to_source_rotation);
                read_numeric_sequence(left_projection["input_resolution"], &left_input_resolution);
                read_numeric_sequence(left_projection["output_resolution"], &left_output_resolution);
            }
            if (!right_projection.empty()) {
                right_projection_presence.focal = !right_projection["focal_px"].empty();
                right_projection_presence.center = !right_projection["center"].empty();
                right_projection_presence.virtual_focal = !right_projection["virtual_focal_px"].empty();
                right_projection_presence.virtual_center = !right_projection["virtual_center"].empty();
                right_projection["model"] >> artifact_out->right_projection_model;
                right_projection["focal_px"] >> artifact_out->right_focal_px;
                read_numeric_sequence(right_projection["center"], &right_center);
                right_projection["virtual_focal_px"] >> artifact_out->right_virtual_focal_px;
                read_numeric_sequence(right_projection["virtual_center"], &right_virtual_center);
                read_matrix_3x3(right_projection["virtual_to_source_rotation"], &artifact_out->right_virtual_to_source_rotation);
                read_numeric_sequence(right_projection["input_resolution"], &right_input_resolution);
                read_numeric_sequence(right_projection["output_resolution"], &right_output_resolution);
            }
            if (left_center.size() >= 2) {
                artifact_out->left_center_x = left_center[0];
                artifact_out->left_center_y = left_center[1];
            }
            if (right_center.size() >= 2) {
                artifact_out->right_center_x = right_center[0];
                artifact_out->right_center_y = right_center[1];
            }
            if (left_virtual_center.size() >= 2) {
                artifact_out->left_virtual_center_x = left_virtual_center[0];
                artifact_out->left_virtual_center_y = left_virtual_center[1];
            }
            if (right_virtual_center.size() >= 2) {
                artifact_out->right_virtual_center_x = right_virtual_center[0];
                artifact_out->right_virtual_center_y = right_virtual_center[1];
            }
            if (left_input_resolution.size() >= 2) {
                artifact_out->left_input_size = cv::Size(
                    static_cast<int>(std::llround(left_input_resolution[0])),
                    static_cast<int>(std::llround(left_input_resolution[1])));
            }
            if (right_input_resolution.size() >= 2) {
                artifact_out->right_input_size = cv::Size(
                    static_cast<int>(std::llround(right_input_resolution[0])),
                    static_cast<int>(std::llround(right_input_resolution[1])));
            }
            if (artifact_out->output_size.width <= 0 && left_output_resolution.size() >= 2) {
                artifact_out->output_size = cv::Size(
                    static_cast<int>(std::llround(left_output_resolution[0])),
                    static_cast<int>(std::llround(left_output_resolution[1])));
            }
            if (artifact_out->output_size.width <= 0 && right_output_resolution.size() >= 2) {
                artifact_out->output_size = cv::Size(
                    static_cast<int>(std::llround(right_output_resolution[0])),
                    static_cast<int>(std::llround(right_output_resolution[1])));
            }
        }

        cv::FileNode canvas_node = fs["canvas"];
        if (!canvas_node.empty()) {
            canvas_node["width"] >> artifact_out->output_size.width;
            canvas_node["height"] >> artifact_out->output_size.height;
        }

        cv::FileNode seam_node = fs["seam"];
        if (!seam_node.empty()) {
            seam_node["transition_px"] >> artifact_out->seam_transition_px;
            seam_node["smoothness_penalty"] >> artifact_out->seam_smoothness_penalty;
            seam_node["temporal_penalty"] >> artifact_out->seam_temporal_penalty;
        }

        cv::FileNode exposure_node = fs["exposure"];
        if (!exposure_node.empty()) {
            exposure_node["enabled"] >> artifact_out->exposure_enabled;
            exposure_node["gain_min"] >> artifact_out->exposure_gain_min;
            exposure_node["gain_max"] >> artifact_out->exposure_gain_max;
            exposure_node["bias_abs_max"] >> artifact_out->exposure_bias_abs_max;
        }
        cv::FileNode crop_node = fs["crop"];
        if (!crop_node.empty()) {
            crop_enabled_present = !crop_node["enabled"].empty();
            crop_node["enabled"] >> artifact_out->crop_enabled;
            cv::FileNode rect_node = crop_node["rect"];
            crop_rect_present = !rect_node.empty();
            if (!rect_node.empty() && rect_node.isSeq()) {
                std::vector<double> crop_rect_values;
                read_numeric_sequence(rect_node, &crop_rect_values);
                if (crop_rect_values.size() >= 4) {
                    artifact_out->crop_rect = cv::Rect(
                        static_cast<int>(std::llround(crop_rect_values[0])),
                        static_cast<int>(std::llround(crop_rect_values[1])),
                        static_cast<int>(std::llround(crop_rect_values[2])),
                        static_cast<int>(std::llround(crop_rect_values[3])));
                }
            }
        }
        cv::FileNode calibration_node = fs["calibration"];
        if (!calibration_node.empty()) {
            cv::FileNode metrics_node = calibration_node["metrics"];
            if (!metrics_node.empty()) {
                double residual_error_px = 0.0;
                cv::FileNode mean_reprojection_error_node = metrics_node["mean_reprojection_error"];
                cv::FileNode reprojection_error_px_node = metrics_node["reprojection_error_px"];
                if (!mean_reprojection_error_node.empty()) {
                    mean_reprojection_error_node >> residual_error_px;
                    artifact_out->residual_alignment_error_px = residual_error_px;
                } else if (!reprojection_error_px_node.empty()) {
                    reprojection_error_px_node >> residual_error_px;
                    artifact_out->residual_alignment_error_px = residual_error_px;
                }
            }
        }
        artifact_out->model = normalize_runtime_geometry_model(artifact_out->model);
        if (artifact_out->model != "virtual-center-rectilinear") {
            return false;
        }

        if (artifact_out->output_size.width <= 0 || artifact_out->output_size.height <= 0) {
            const int base_width = std::max(
                1,
                std::max(
                    artifact_out->left_input_size.width,
                    artifact_out->right_input_size.width));
            const int base_height = std::max(
                1,
                std::max(
                    artifact_out->left_input_size.height,
                    artifact_out->right_input_size.height));
            artifact_out->output_size = cv::Size(base_width, base_height);
        }
        artifact_out->left_projection_model = normalize_projection_model(artifact_out->left_projection_model);
        artifact_out->right_projection_model = normalize_projection_model(artifact_out->right_projection_model);
        if (!validate_rectilinear_operator_geometry_or_fail()) {
            return false;
        }
        if (artifact_out->left_focal_px <= 0.0) {
            artifact_out->left_focal_px = artifact_out->right_focal_px;
        }
        if (artifact_out->right_focal_px <= 0.0) {
            artifact_out->right_focal_px = artifact_out->left_focal_px;
        }
        if (artifact_out->left_center_x <= 0.0 && artifact_out->right_center_x > 0.0) {
            artifact_out->left_center_x = artifact_out->right_center_x;
            artifact_out->left_center_y = artifact_out->right_center_y;
        }
        if (artifact_out->right_center_x <= 0.0 && artifact_out->left_center_x > 0.0) {
            artifact_out->right_center_x = artifact_out->left_center_x;
            artifact_out->right_center_y = artifact_out->left_center_y;
        }
        if (artifact_out->residual_model.empty()) {
            artifact_out->residual_model = "rigid";
        }
        if (artifact_out->residual_model != "rigid") {
            return false;
        }
        sanitize_virtual_projection_side(
            &artifact_out->left_focal_px,
            &artifact_out->left_center_x,
            &artifact_out->left_center_y,
            &artifact_out->left_virtual_focal_px,
            &artifact_out->left_virtual_center_x,
            &artifact_out->left_virtual_center_y,
            artifact_out->left_input_size,
            artifact_out->output_size);
        sanitize_virtual_projection_side(
            &artifact_out->right_focal_px,
            &artifact_out->right_center_x,
            &artifact_out->right_center_y,
            &artifact_out->right_virtual_focal_px,
            &artifact_out->right_virtual_center_x,
            &artifact_out->right_virtual_center_y,
            artifact_out->right_input_size,
            artifact_out->output_size);
        if (artifact_out->left_virtual_to_source_rotation.empty()) {
            artifact_out->left_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
        }
        if (artifact_out->right_virtual_to_source_rotation.empty()) {
            artifact_out->right_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
        }
        if (artifact_out->alignment_matrix.empty()) {
            artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
        }
        sanitize_crop_rect();
        return true;
    } catch (const cv::Exception&) {
        // Fall through to permissive parsing below.
    }

    std::ifstream file(path);
    if (!file.is_open()) {
        return false;
    }
    const std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (text.find("\"artifact_type\"") == std::string::npos) {
        return false;
    }

    extract_json_string_for_key(text, "\"model\"", &artifact_out->model);
    extract_json_string_for_key(text, "\"alignment_model\"", &artifact_out->alignment_model);
    extract_json_string_for_key(text, "\"residual_model\"", &artifact_out->residual_model);
    if (artifact_out->alignment_model.empty()) {
        artifact_out->alignment_model = "rigid";
    }
    if (artifact_out->model.empty()) {
        artifact_out->model = "virtual-center-rectilinear";
    }
    artifact_out->model = normalize_runtime_geometry_model(artifact_out->model);
    if (artifact_out->model != "virtual-center-rectilinear") {
        return false;
    }

    std::string alignment_text;
    if (extract_json_array_for_key(text, "\"alignment\"", &alignment_text)) {
        std::vector<double> values;
        if (parse_numeric_vector(alignment_text, &values)) {
            if (values.size() >= 9) {
                artifact_out->alignment_matrix = cv::Mat(3, 3, CV_64F, values.data()).clone();
            } else if (values.size() >= 6) {
                artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
                artifact_out->alignment_matrix.at<double>(0, 0) = values[0];
                artifact_out->alignment_matrix.at<double>(0, 1) = values[1];
                artifact_out->alignment_matrix.at<double>(0, 2) = values[2];
                artifact_out->alignment_matrix.at<double>(1, 0) = values[3];
                artifact_out->alignment_matrix.at<double>(1, 1) = values[4];
                artifact_out->alignment_matrix.at<double>(1, 2) = values[5];
            }
        }
    }
    if (artifact_out->alignment_matrix.empty()) {
        artifact_out->alignment_matrix = cv::Mat::eye(3, 3, CV_64F);
    }
    if (artifact_out->residual_model.empty()) {
        artifact_out->residual_model = "rigid";
    }
    if (artifact_out->residual_model != "rigid") {
        return false;
    }

    double numeric_value = 0.0;
    if (extract_json_number_for_key(text, "\"focal_px\"", &numeric_value)) {
        artifact_out->left_focal_px = numeric_value;
        artifact_out->right_focal_px = numeric_value;
        left_projection_presence.focal = true;
        right_projection_presence.focal = true;
    }
    if (extract_json_number_for_key(text, "\"transition_px\"", &numeric_value)) {
        artifact_out->seam_transition_px = static_cast<int>(std::llround(numeric_value));
    }
    if (extract_json_number_for_key(text, "\"smoothness_penalty\"", &numeric_value)) {
        artifact_out->seam_smoothness_penalty = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"temporal_penalty\"", &numeric_value)) {
        artifact_out->seam_temporal_penalty = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"gain_min\"", &numeric_value)) {
        artifact_out->exposure_gain_min = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"gain_max\"", &numeric_value)) {
        artifact_out->exposure_gain_max = numeric_value;
    }
    if (extract_json_number_for_key(text, "\"bias_abs_max\"", &numeric_value)) {
        artifact_out->exposure_bias_abs_max = numeric_value;
    }
    std::string exposure_block_text;
    bool exposure_enabled = artifact_out->exposure_enabled;
    if (extract_json_object_for_key(text, "\"exposure\"", &exposure_block_text) &&
        extract_json_bool(exposure_block_text, "\"enabled\"", &exposure_enabled)) {
        artifact_out->exposure_enabled = exposure_enabled;
    }
    std::string crop_block_text;
    if (extract_json_object_for_key(text, "\"crop\"", &crop_block_text)) {
        bool parsed_crop_enabled = false;
        if (extract_json_bool(crop_block_text, "\"enabled\"", &parsed_crop_enabled)) {
            artifact_out->crop_enabled = parsed_crop_enabled;
            crop_enabled_present = true;
        }
        std::string crop_rect_text;
        if (extract_json_array_for_key(crop_block_text, "\"rect\"", &crop_rect_text)) {
            crop_rect_present = true;
            std::vector<double> crop_rect_values;
            if (parse_numeric_vector(crop_rect_text, &crop_rect_values) && crop_rect_values.size() >= 4) {
                artifact_out->crop_rect = cv::Rect(
                    static_cast<int>(std::llround(crop_rect_values[0])),
                    static_cast<int>(std::llround(crop_rect_values[1])),
                    static_cast<int>(std::llround(crop_rect_values[2])),
                    static_cast<int>(std::llround(crop_rect_values[3])));
            }
        }
    }
    if (extract_json_number_for_key(text, "\"mean_reprojection_error\"", &numeric_value)) {
        artifact_out->residual_alignment_error_px = numeric_value;
    } else if (extract_json_number_for_key(text, "\"reprojection_error_px\"", &numeric_value)) {
        artifact_out->residual_alignment_error_px = numeric_value;
    }
    std::string mesh_block_text;
    if (extract_json_object_for_key(text, "\"mesh\"", &mesh_block_text)) {
        if (extract_json_number_for_key(mesh_block_text, "\"grid_cols\"", &numeric_value)) {
            artifact_out->mesh_grid_cols = static_cast<int>(std::llround(numeric_value));
        }
        if (extract_json_number_for_key(mesh_block_text, "\"grid_rows\"", &numeric_value)) {
            artifact_out->mesh_grid_rows = static_cast<int>(std::llround(numeric_value));
        }
        extract_json_bool(mesh_block_text, "\"fallback_used\"", &artifact_out->mesh_fallback_used);
        if (extract_json_number_for_key(mesh_block_text, "\"max_displacement_px\"", &numeric_value)) {
            artifact_out->mesh_max_displacement_px = numeric_value;
        }
        if (extract_json_number_for_key(mesh_block_text, "\"max_local_scale_drift\"", &numeric_value)) {
            artifact_out->mesh_max_local_scale_drift = numeric_value;
        }
        if (extract_json_number_for_key(mesh_block_text, "\"max_local_rotation_drift\"", &numeric_value)) {
            artifact_out->mesh_max_local_rotation_drift = numeric_value;
        }
        auto parse_grid_array = [&](const std::string& key, cv::Mat* grid_out) {
            if (grid_out == nullptr) {
                return false;
            }
            std::string grid_text;
            if (!extract_json_array_for_key(mesh_block_text, key, &grid_text)) {
                return false;
            }
            std::vector<double> parsed_values;
            if (!parse_numeric_vector(grid_text, &parsed_values)) {
                return false;
            }
            const int rows = artifact_out->mesh_grid_rows + 1;
            const int cols = artifact_out->mesh_grid_cols + 1;
            if (rows <= 0 || cols <= 0 || static_cast<int>(parsed_values.size()) != rows * cols) {
                return false;
            }
            cv::Mat grid(rows, cols, CV_32F);
            for (int row = 0; row < rows; ++row) {
                auto* grid_row = grid.ptr<float>(row);
                for (int col = 0; col < cols; ++col) {
                    grid_row[col] = static_cast<float>(parsed_values[static_cast<std::size_t>(row * cols + col)]);
                }
            }
            *grid_out = grid;
            return true;
        };
        parse_grid_array("\"control_displacement_x\"", &artifact_out->mesh_control_displacement_x);
        parse_grid_array("\"control_displacement_y\"", &artifact_out->mesh_control_displacement_y);
    }
    if (artifact_out->output_size.width <= 0 || artifact_out->output_size.height <= 0) {
        artifact_out->output_size = cv::Size(artifact_out->left_input_size.width, artifact_out->left_input_size.height);
    }
    std::string projection_block_text;
    const std::string& projection_lookup_text =
        (extract_json_object_for_key(text, "\"projection\"", &projection_block_text) && !projection_block_text.empty())
            ? projection_block_text
            : text;
    std::string left_projection_text;
    if (extract_json_object_for_key(projection_lookup_text, "\"left\"", &left_projection_text)) {
        extract_json_string_for_key(left_projection_text, "\"model\"", &artifact_out->left_projection_model);
        if (extract_json_number_for_key(left_projection_text, "\"focal_px\"", &numeric_value)) {
            artifact_out->left_focal_px = numeric_value;
            left_projection_presence.focal = true;
        }
        std::string center_text;
        if (extract_json_array_for_key(left_projection_text, "\"center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->left_center_x = center_values[0];
                artifact_out->left_center_y = center_values[1];
                left_projection_presence.center = true;
            }
        }
        if (extract_json_number_for_key(left_projection_text, "\"virtual_focal_px\"", &numeric_value)) {
            artifact_out->left_virtual_focal_px = numeric_value;
            left_projection_presence.virtual_focal = true;
        }
        if (extract_json_array_for_key(left_projection_text, "\"virtual_center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->left_virtual_center_x = center_values[0];
                artifact_out->left_virtual_center_y = center_values[1];
                left_projection_presence.virtual_center = true;
            }
        }
        if (extract_json_array_for_key(left_projection_text, "\"virtual_to_source_rotation\"", &center_text)) {
            std::vector<double> rotation_values;
            if (parse_numeric_vector(center_text, &rotation_values) && rotation_values.size() >= 9) {
                artifact_out->left_virtual_to_source_rotation =
                    cv::Mat(3, 3, CV_64F, rotation_values.data()).clone();
            }
        }
    }
    std::string right_projection_text;
    if (extract_json_object_for_key(projection_lookup_text, "\"right\"", &right_projection_text)) {
        extract_json_string_for_key(right_projection_text, "\"model\"", &artifact_out->right_projection_model);
        if (extract_json_number_for_key(right_projection_text, "\"focal_px\"", &numeric_value)) {
            artifact_out->right_focal_px = numeric_value;
            right_projection_presence.focal = true;
        }
        std::string center_text;
        if (extract_json_array_for_key(right_projection_text, "\"center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->right_center_x = center_values[0];
                artifact_out->right_center_y = center_values[1];
                right_projection_presence.center = true;
            }
        }
        if (extract_json_number_for_key(right_projection_text, "\"virtual_focal_px\"", &numeric_value)) {
            artifact_out->right_virtual_focal_px = numeric_value;
            right_projection_presence.virtual_focal = true;
        }
        if (extract_json_array_for_key(right_projection_text, "\"virtual_center\"", &center_text)) {
            std::vector<double> center_values;
            if (parse_numeric_vector(center_text, &center_values) && center_values.size() >= 2) {
                artifact_out->right_virtual_center_x = center_values[0];
                artifact_out->right_virtual_center_y = center_values[1];
                right_projection_presence.virtual_center = true;
            }
        }
        if (extract_json_array_for_key(right_projection_text, "\"virtual_to_source_rotation\"", &center_text)) {
            std::vector<double> rotation_values;
            if (parse_numeric_vector(center_text, &rotation_values) && rotation_values.size() >= 9) {
                artifact_out->right_virtual_to_source_rotation =
                    cv::Mat(3, 3, CV_64F, rotation_values.data()).clone();
            }
        }
    }
    artifact_out->left_projection_model = normalize_projection_model(artifact_out->left_projection_model);
    artifact_out->right_projection_model = normalize_projection_model(artifact_out->right_projection_model);
    if (!validate_rectilinear_operator_geometry_or_fail()) {
        return false;
    }
    if (artifact_out->left_focal_px <= 0.0) {
        artifact_out->left_focal_px = artifact_out->right_focal_px;
    }
    if (artifact_out->right_focal_px <= 0.0) {
        artifact_out->right_focal_px = artifact_out->left_focal_px;
    }
    if (artifact_out->left_center_x <= 0.0 && artifact_out->right_center_x > 0.0) {
        artifact_out->left_center_x = artifact_out->right_center_x;
        artifact_out->left_center_y = artifact_out->right_center_y;
    }
    if (artifact_out->right_center_x <= 0.0 && artifact_out->left_center_x > 0.0) {
        artifact_out->right_center_x = artifact_out->left_center_x;
        artifact_out->right_center_y = artifact_out->left_center_y;
    }
    sanitize_virtual_projection_side(
        &artifact_out->left_focal_px,
        &artifact_out->left_center_x,
        &artifact_out->left_center_y,
        &artifact_out->left_virtual_focal_px,
        &artifact_out->left_virtual_center_x,
        &artifact_out->left_virtual_center_y,
        artifact_out->left_input_size,
        artifact_out->output_size);
    sanitize_virtual_projection_side(
        &artifact_out->right_focal_px,
        &artifact_out->right_center_x,
        &artifact_out->right_center_y,
        &artifact_out->right_virtual_focal_px,
        &artifact_out->right_virtual_center_x,
        &artifact_out->right_virtual_center_y,
        artifact_out->right_input_size,
        artifact_out->output_size);
    if (artifact_out->left_virtual_to_source_rotation.empty()) {
        artifact_out->left_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
    }
    if (artifact_out->right_virtual_to_source_rotation.empty()) {
        artifact_out->right_virtual_to_source_rotation = cv::Mat::eye(3, 3, CV_64F);
    }
    sanitize_crop_rect();
    return true;
}


}  // namespace hogak::engine
