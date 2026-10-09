#pragma once
#include "Json.h"
#include <winrt/Windows.Web.Http.h>
#include <winrt/Windows.Web.Http.Filters.h>
#include <winrt/Windows.Web.Http.Headers.h>
#include <filesystem>
#include <fstream>
#include <shellapi.h>
namespace amiebl {
// HTTP continuations are asynchronous; mutation requests are never retried.
struct CoreClient {
    winrt::Windows::Web::Http::HttpClient http{nullptr};
    std::filesystem::path dataDir;
    int port = 8080;
    winrt::hstring token;
    HANDLE process = nullptr, job = nullptr;
    CoreClient();
    ~CoreClient();
    winrt::Windows::Foundation::IAsyncAction Connect();
    winrt::Windows::Foundation::IAsyncOperation<winrt::Windows::Data::Json::JsonObject>
    Request(winrt::hstring method, winrt::hstring path,
            winrt::Windows::Data::Json::JsonObject payload = {});
    winrt::Windows::Foundation::IAsyncAction Shutdown();
    bool OwnedExited() const;
    static std::wstring Option(std::wstring const &name);

  private:
    winrt::Windows::Foundation::IAsyncOperation<bool> Compatible();
};
} // namespace amiebl
