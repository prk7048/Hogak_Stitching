#pragma once

#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <utility>
#include <vector>

namespace hogak::control::json {

class Value {
public:
    using Object = std::map<std::string, Value>;
    using Array = std::vector<Value>;

    Value() : data_(nullptr) {}
    explicit Value(std::nullptr_t) : data_(nullptr) {}
    explicit Value(bool value) : data_(value) {}
    explicit Value(double value) : data_(value) {}
    explicit Value(std::string value) : data_(std::move(value)) {}
    explicit Value(Object value) : data_(std::move(value)) {}
    explicit Value(Array value) : data_(std::move(value)) {}

    bool is_null() const noexcept { return std::holds_alternative<std::nullptr_t>(data_); }
    bool is_bool() const noexcept { return std::holds_alternative<bool>(data_); }
    bool is_number() const noexcept { return std::holds_alternative<double>(data_); }
    bool is_string() const noexcept { return std::holds_alternative<std::string>(data_); }
    bool is_object() const noexcept { return std::holds_alternative<Object>(data_); }
    bool is_array() const noexcept { return std::holds_alternative<Array>(data_); }

    bool as_bool() const { return std::get<bool>(data_); }
    double as_number() const { return std::get<double>(data_); }
    const std::string& as_string() const { return std::get<std::string>(data_); }
    const Object& as_object() const { return std::get<Object>(data_); }
    const Array& as_array() const { return std::get<Array>(data_); }

    const Value* find(const std::string& key) const {
        if (!is_object()) {
            return nullptr;
        }
        const auto& object = std::get<Object>(data_);
        const auto it = object.find(key);
        return it == object.end() ? nullptr : &it->second;
    }

private:
    std::variant<std::nullptr_t, bool, double, std::string, Object, Array> data_;
};

class Parser {
public:
    explicit Parser(std::string_view text) : text_(text) {}

    bool parse(Value* value_out, std::string* error_out) {
        skip_ws();
        if (!parse_value(value_out, error_out)) {
            return false;
        }
        skip_ws();
        if (!at_end()) {
            if (error_out != nullptr) {
                *error_out = "unexpected trailing characters after JSON value";
            }
            return false;
        }
        return true;
    }

private:
    std::string_view text_;
    std::size_t pos_ = 0;

    bool at_end() const noexcept { return pos_ >= text_.size(); }

    char peek() const noexcept {
        return at_end() ? '\0' : text_[pos_];
    }

    char take() noexcept {
        return at_end() ? '\0' : text_[pos_++];
    }

    void skip_ws() noexcept {
        while (!at_end()) {
            const char ch = text_[pos_];
            if (ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n') {
                ++pos_;
                continue;
            }
            break;
        }
    }

    static void append_utf8(std::uint32_t code_point, std::string* out) {
        if (code_point <= 0x7F) {
            out->push_back(static_cast<char>(code_point));
        } else if (code_point <= 0x7FF) {
            out->push_back(static_cast<char>(0xC0 | ((code_point >> 6) & 0x1F)));
            out->push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
        } else if (code_point <= 0xFFFF) {
            out->push_back(static_cast<char>(0xE0 | ((code_point >> 12) & 0x0F)));
            out->push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3F)));
            out->push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
        } else {
            out->push_back(static_cast<char>(0xF0 | ((code_point >> 18) & 0x07)));
            out->push_back(static_cast<char>(0x80 | ((code_point >> 12) & 0x3F)));
            out->push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3F)));
            out->push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
        }
    }

    static std::optional<std::uint32_t> hex4_to_code_point(std::string_view text) {
        if (text.size() != 4) {
            return std::nullopt;
        }
        std::uint32_t value = 0;
        for (const char ch : text) {
            value <<= 4;
            if (ch >= '0' && ch <= '9') {
                value |= static_cast<std::uint32_t>(ch - '0');
            } else if (ch >= 'a' && ch <= 'f') {
                value |= static_cast<std::uint32_t>(10 + (ch - 'a'));
            } else if (ch >= 'A' && ch <= 'F') {
                value |= static_cast<std::uint32_t>(10 + (ch - 'A'));
            } else {
                return std::nullopt;
            }
        }
        return value;
    }

    bool parse_string(std::string* value_out, std::string* error_out) {
        if (take() != '"') {
            if (error_out != nullptr) {
                *error_out = "expected string";
            }
            return false;
        }
        std::string result;
        while (!at_end()) {
            const char ch = take();
            if (ch == '"') {
                if (value_out != nullptr) {
                    *value_out = std::move(result);
                }
                return true;
            }
            if (static_cast<unsigned char>(ch) < 0x20) {
                if (error_out != nullptr) {
                    *error_out = "invalid control character in JSON string";
                }
                return false;
            }
            if (ch != '\\') {
                result.push_back(ch);
                continue;
            }
            if (at_end()) {
                if (error_out != nullptr) {
                    *error_out = "unterminated escape sequence in JSON string";
                }
                return false;
            }
            const char esc = take();
            switch (esc) {
                case '"': result.push_back('"'); break;
                case '\\': result.push_back('\\'); break;
                case '/': result.push_back('/'); break;
                case 'b': result.push_back('\b'); break;
                case 'f': result.push_back('\f'); break;
                case 'n': result.push_back('\n'); break;
                case 'r': result.push_back('\r'); break;
                case 't': result.push_back('\t'); break;
                case 'u': {
                    if (pos_ + 4 > text_.size()) {
                        if (error_out != nullptr) {
                            *error_out = "invalid unicode escape in JSON string";
                        }
                        return false;
                    }
                    const auto first = hex4_to_code_point(text_.substr(pos_, 4));
                    if (!first.has_value()) {
                        if (error_out != nullptr) {
                            *error_out = "invalid unicode escape in JSON string";
                        }
                        return false;
                    }
                    pos_ += 4;
                    std::uint32_t code_point = *first;
                    if (code_point >= 0xD800 && code_point <= 0xDBFF) {
                        if (pos_ + 6 > text_.size() || text_[pos_] != '\\' || text_[pos_ + 1] != 'u') {
                            if (error_out != nullptr) {
                                *error_out = "invalid unicode surrogate pair in JSON string";
                            }
                            return false;
                        }
                        const auto second = hex4_to_code_point(text_.substr(pos_ + 2, 4));
                        if (!second.has_value() || *second < 0xDC00 || *second > 0xDFFF) {
                            if (error_out != nullptr) {
                                *error_out = "invalid unicode surrogate pair in JSON string";
                            }
                            return false;
                        }
                        pos_ += 6;
                        code_point = 0x10000 + (((code_point - 0xD800) << 10) | (*second - 0xDC00));
                    }
                    append_utf8(code_point, &result);
                    break;
                }
                default:
                    if (error_out != nullptr) {
                        *error_out = "invalid escape sequence in JSON string";
                    }
                    return false;
            }
        }
        if (error_out != nullptr) {
            *error_out = "unterminated JSON string";
        }
        return false;
    }

    bool parse_number(double* value_out, std::string* error_out) {
        const std::size_t start = pos_;
        if (peek() == '-') {
            ++pos_;
        }
        if (at_end()) {
            if (error_out != nullptr) {
                *error_out = "invalid JSON number";
            }
            return false;
        }
        if (peek() == '0') {
            ++pos_;
        } else if (peek() >= '1' && peek() <= '9') {
            while (!at_end() && std::isdigit(static_cast<unsigned char>(peek())) != 0) {
                ++pos_;
            }
        } else {
            if (error_out != nullptr) {
                *error_out = "invalid JSON number";
            }
            return false;
        }
        if (!at_end() && peek() == '.') {
            ++pos_;
            if (at_end() || std::isdigit(static_cast<unsigned char>(peek())) == 0) {
                if (error_out != nullptr) {
                    *error_out = "invalid JSON number";
                }
                return false;
            }
            while (!at_end() && std::isdigit(static_cast<unsigned char>(peek())) != 0) {
                ++pos_;
            }
        }
        if (!at_end() && (peek() == 'e' || peek() == 'E')) {
            ++pos_;
            if (!at_end() && (peek() == '+' || peek() == '-')) {
                ++pos_;
            }
            if (at_end() || std::isdigit(static_cast<unsigned char>(peek())) == 0) {
                if (error_out != nullptr) {
                    *error_out = "invalid JSON number";
                }
                return false;
            }
            while (!at_end() && std::isdigit(static_cast<unsigned char>(peek())) != 0) {
                ++pos_;
            }
        }
        const std::string token(text_.substr(start, pos_ - start));
        char* end_ptr = nullptr;
        const double parsed = std::strtod(token.c_str(), &end_ptr);
        if (end_ptr == nullptr || *end_ptr != '\0') {
            if (error_out != nullptr) {
                *error_out = "invalid JSON number";
            }
            return false;
        }
        if (value_out != nullptr) {
            *value_out = parsed;
        }
        return true;
    }

    bool parse_array(Value* value_out, std::string* error_out) {
        if (take() != '[') {
            if (error_out != nullptr) {
                *error_out = "expected array";
            }
            return false;
        }
        Value::Array values;
        skip_ws();
        if (peek() == ']') {
            ++pos_;
            if (value_out != nullptr) {
                *value_out = Value(std::move(values));
            }
            return true;
        }
        while (true) {
            Value item;
            if (!parse_value(&item, error_out)) {
                return false;
            }
            values.push_back(std::move(item));
            skip_ws();
            if (peek() == ',') {
                ++pos_;
                skip_ws();
                continue;
            }
            if (peek() == ']') {
                ++pos_;
                if (value_out != nullptr) {
                    *value_out = Value(std::move(values));
                }
                return true;
            }
            if (error_out != nullptr) {
                *error_out = "expected ',' or ']' in JSON array";
            }
            return false;
        }
    }

    bool parse_object(Value* value_out, std::string* error_out) {
        if (take() != '{') {
            if (error_out != nullptr) {
                *error_out = "expected object";
            }
            return false;
        }
        Value::Object object;
        skip_ws();
        if (peek() == '}') {
            ++pos_;
            if (value_out != nullptr) {
                *value_out = Value(std::move(object));
            }
            return true;
        }
        while (true) {
            std::string key;
            if (!parse_string(&key, error_out)) {
                return false;
            }
            skip_ws();
            if (take() != ':') {
                if (error_out != nullptr) {
                    *error_out = "expected ':' after JSON object key";
                }
                return false;
            }
            skip_ws();
            Value item;
            if (!parse_value(&item, error_out)) {
                return false;
            }
            object.emplace(std::move(key), std::move(item));
            skip_ws();
            if (peek() == ',') {
                ++pos_;
                skip_ws();
                continue;
            }
            if (peek() == '}') {
                ++pos_;
                if (value_out != nullptr) {
                    *value_out = Value(std::move(object));
                }
                return true;
            }
            if (error_out != nullptr) {
                *error_out = "expected ',' or '}' in JSON object";
            }
            return false;
        }
    }

    bool parse_value(Value* value_out, std::string* error_out) {
        skip_ws();
        if (at_end()) {
            if (error_out != nullptr) {
                *error_out = "unexpected end of JSON input";
            }
            return false;
        }
        const char ch = peek();
        if (ch == '{') {
            return parse_object(value_out, error_out);
        }
        if (ch == '[') {
            return parse_array(value_out, error_out);
        }
        if (ch == '"') {
            std::string text;
            if (!parse_string(&text, error_out)) {
                return false;
            }
            if (value_out != nullptr) {
                *value_out = Value(std::move(text));
            }
            return true;
        }
        if (ch == 't') {
            if (text_.substr(pos_, 4) != "true") {
                if (error_out != nullptr) {
                    *error_out = "invalid literal";
                }
                return false;
            }
            pos_ += 4;
            if (value_out != nullptr) {
                *value_out = Value(true);
            }
            return true;
        }
        if (ch == 'f') {
            if (text_.substr(pos_, 5) != "false") {
                if (error_out != nullptr) {
                    *error_out = "invalid literal";
                }
                return false;
            }
            pos_ += 5;
            if (value_out != nullptr) {
                *value_out = Value(false);
            }
            return true;
        }
        if (ch == 'n') {
            if (text_.substr(pos_, 4) != "null") {
                if (error_out != nullptr) {
                    *error_out = "invalid literal";
                }
                return false;
            }
            pos_ += 4;
            if (value_out != nullptr) {
                *value_out = Value(nullptr);
            }
            return true;
        }
        if (ch == '-' || std::isdigit(static_cast<unsigned char>(ch)) != 0) {
            double parsed = 0.0;
            if (!parse_number(&parsed, error_out)) {
                return false;
            }
            if (value_out != nullptr) {
                *value_out = Value(parsed);
            }
            return true;
        }
        if (error_out != nullptr) {
            *error_out = "unexpected token in JSON input";
        }
        return false;
    }
};

inline bool parse(std::string_view text, Value* value_out, std::string* error_out) {
    Parser parser(text);
    return parser.parse(value_out, error_out);
}

inline const Value* object_find(const Value& value, const char* key) {
    return value.find(key);
}

}  // namespace hogak::control::json
