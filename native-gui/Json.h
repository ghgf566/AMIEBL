#pragma once
#include <winrt/Windows.Data.Json.h>
#include <cmath>
#include <sstream>
#include <iomanip>
namespace amiebl {
using namespace winrt;
using namespace Windows::Data::Json;
inline JsonObject clone(JsonObject const &o) { return JsonObject::Parse(o.Stringify()); }
inline IJsonValue value(JsonObject const &o, hstring const &k) {
    return o.HasKey(k) ? o.GetNamedValue(k) : JsonValue::CreateNullValue();
}
inline hstring str(JsonObject const &o, hstring const &k, hstring const &fallback = L"") {
    auto v = value(o, k);
    if (v.ValueType() == JsonValueType::Null)
        return fallback;
    return v.ValueType() == JsonValueType::String ? v.GetString() : v.Stringify();
}
inline bool flag(JsonObject const &o, hstring const &k, bool fallback = false) {
    auto v = value(o, k);
    return v.ValueType() == JsonValueType::Boolean ? v.GetBoolean() : fallback;
}
inline double number(JsonObject const &o, hstring const &k, double fallback = 0) {
    auto v = value(o, k);
    return v.ValueType() == JsonValueType::Number ? v.GetNumber() : fallback;
}
inline JsonArray array(JsonObject const &o, hstring const &k) {
    auto v = value(o, k);
    return v.ValueType() == JsonValueType::Array ? v.GetArray() : JsonArray();
}
inline JsonObject object(JsonObject const &o, hstring const &k) {
    auto v = value(o, k);
    return v.ValueType() == JsonValueType::Object ? v.GetObject() : JsonObject();
}
inline JsonObject entity(JsonObject const &o, hstring const &collection, hstring const &id) {
    for (auto v : array(o, collection))
        if (v.ValueType() == JsonValueType::Object && str(v.GetObject(), L"id") == id)
            return v.GetObject();
    return JsonObject();
}
inline hstring metric(JsonObject const &o, hstring const &k, hstring const &suffix = L"",
                      hstring const &format = L"0.0") {
    auto v = value(o, k);
    if (v.ValueType() != JsonValueType::Number)
        return L"未提供";
    std::wostringstream s;
    s << std::fixed << std::setprecision(format == L"0" ? 0 : 1) << v.GetNumber();
    return hstring(s.str()) + suffix;
}
inline bool equal(IJsonValue const &a, IJsonValue const &b) {
    if (a.ValueType() != b.ValueType())
        return false;
    if (a.ValueType() == JsonValueType::Object) {
        auto x = a.GetObject(), y = b.GetObject();
        if (x.Size() != y.Size())
            return false;
        for (auto p : x)
            if (!y.HasKey(p.Key()) || !equal(p.Value(), y.Lookup(p.Key())))
                return false;
        return true;
    }
    if (a.ValueType() == JsonValueType::Array) {
        auto x = a.GetArray(), y = b.GetArray();
        if (x.Size() != y.Size())
            return false;
        for (uint32_t i = 0; i < x.Size(); i++)
            if (!equal(x.GetAt(i), y.GetAt(i)))
                return false;
        return true;
    }
    return a.Stringify() == b.Stringify();
}
inline JsonObject body(hstring const &key, IJsonValue const &v) {
    JsonObject b;
    b.SetNamedValue(key, v);
    return b;
}
} // namespace amiebl
