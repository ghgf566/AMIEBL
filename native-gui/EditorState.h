#pragma once
#include "Json.h"
#include <cwctype>
#include <functional>
#include <locale>
#include <vector>
namespace amiebl {
enum class FieldKind { Text, Integer, Number, Boolean, Choice, Multiline };
struct FieldSpec {
    winrt::hstring key, label;
    FieldKind kind = FieldKind::Text;
    bool optional = false;
    double minimum = 0, maximum = 1048576;
    std::vector<winrt::hstring> options;
    std::function<bool(JsonObject const &)> enabled;
};
inline winrt::hstring trim(winrt::hstring const &s) {
    std::wstring t(s);
    auto a = t.find_first_not_of(L" \t\r\n"), b = t.find_last_not_of(L" \t\r\n");
    return a == std::wstring::npos ? winrt::hstring() : winrt::hstring(t.substr(a, b - a + 1));
}
inline JsonArray split(winrt::hstring const &text, wchar_t separator) {
    JsonArray result;
    std::wistringstream stream{std::wstring(text)};
    std::wstring part;
    while (std::getline(stream, part, separator)) {
        auto item = trim(winrt::hstring(part));
        if (!item.empty())
            result.Append(JsonValue::CreateStringValue(item));
    }
    return result;
}
struct EditorField {
    FieldSpec spec;
    winrt::hstring text;
    IJsonValue Parse() const {
        auto t = trim(text);
        if (spec.optional && t.empty())
            return JsonValue::CreateNullValue();
        if (spec.kind == FieldKind::Boolean)
            return JsonValue::CreateBooleanValue(text == L"true");
        if (spec.kind == FieldKind::Integer || spec.kind == FieldKind::Number) {
            std::wistringstream input{std::wstring(t)};
            input.imbue(std::locale::classic());
            double n = 0;
            if (!(input >> n) || !input.eof() || !std::isfinite(n) || n < spec.minimum ||
                n > spec.maximum ||
                (spec.kind == FieldKind::Integer &&
                 (std::floor(n) != n ||
                  std::wstring(t).find_first_of(L".eE") != std::wstring::npos)))
                throw winrt::hresult_error(E_INVALIDARG,
                                           L"「" + spec.label + L"」格式或範圍不正確（" +
                                               winrt::to_hstring(spec.minimum) + L"～" +
                                               winrt::to_hstring(spec.maximum) + L"）。");
            return JsonValue::CreateNumberValue(n);
        }
        if (spec.kind == FieldKind::Choice && !spec.options.empty() &&
            std::find(spec.options.begin(), spec.options.end(), text) == spec.options.end())
            throw winrt::hresult_error(E_INVALIDARG, L"「" + spec.label + L"」格式或範圍不正確。");
        return JsonValue::CreateStringValue(t);
    }
};
struct EditorDraft {
    winrt::hstring collection, id;
    JsonObject original;
    std::vector<EditorField> fields;
    bool dirty = false;
    uint64_t revision = 0;
    EditorDraft(winrt::hstring c, JsonObject const &source, std::vector<FieldSpec> specs)
        : collection(c), id(str(source, L"id")), original(clone(source)) {
        for (auto const &spec : specs) {
            auto v = value(source, spec.key);
            winrt::hstring text;
            if (v.ValueType() == JsonValueType::Array) {
                for (auto item : v.GetArray()) {
                    if (!text.empty())
                        text = text + (spec.key == L"model_dirs" ? L"\n" : L",");
                    text = text + (item.ValueType() == JsonValueType::String ? item.GetString()
                                                                             : item.Stringify());
                }
            } else
                text = str(source, spec.key);
            fields.push_back({spec, text});
        }
    }
    EditorField &Field(winrt::hstring key) {
        for (auto &f : fields)
            if (f.spec.key == key)
                return f;
        throw winrt::hresult_error(E_INVALIDARG, L"不存在的設定欄位：" + key);
    }
    void Edit(winrt::hstring key, winrt::hstring text) {
        auto &f = Field(key);
        if (f.text != text) {
            f.text = text;
            dirty = true;
            revision++;
        }
    }
    JsonObject Preview(bool validate = false) const {
        auto data = clone(original);
        for (auto const &f : fields) {
            try {
                data.SetNamedValue(f.spec.key, f.Parse());
            } catch (winrt::hresult_error const &) {
                if (validate)
                    throw;
                data.SetNamedValue(f.spec.key, JsonValue::CreateStringValue(f.text));
            }
        }
        if (validate) {
            if (collection != L"system" && trim(str(data, L"name")).empty())
                throw winrt::hresult_error(E_INVALIDARG, L"名稱不可留白。");
            for (auto key : {L"agent_tools", L"model_dirs"})
                for (auto const &f : fields)
                    if (f.spec.key == key)
                        data.SetNamedValue(
                            key, split(f.text, std::wstring(key) == L"model_dirs" ? L'\n' : L','));
        }
        return data;
    }
    // Compare only edited fields against a fresh server snapshot. Unknown fields
    // and unrelated concurrent edits remain in that snapshot, exactly as v1.0.
    void Merge(JsonObject const &latest, JsonObject const &changed) const {
        auto target = collection == L"system" ? latest : entity(latest, collection, id);
        if (collection != L"system" && target.Size() == 0)
            throw winrt::hresult_error(E_FAIL, L"此項目已被移除，請重新整理。");
        for (auto const &f : fields) {
            auto key = f.spec.key;
            if (equal(value(original, key), value(changed, key)))
                continue;
            if (!equal(value(original, key), value(target, key)) &&
                !equal(value(changed, key), value(target, key)))
                throw winrt::hresult_error(
                    E_FAIL, L"「" + f.spec.label +
                                L"」已被其他操作修改，請重新整理後再編輯；目前草稿仍保留。");
            target.SetNamedValue(key, value(changed, key));
        }
        if (collection != L"system")
            replace_entity(latest, collection, target);
    }
};
std::vector<FieldSpec> EditorSchema(winrt::hstring collection, JsonObject const &config);
winrt::hstring FieldSection(winrt::hstring key, winrt::hstring collection);
winrt::hstring FieldHelp(winrt::hstring key, winrt::hstring collection);
} // namespace amiebl
