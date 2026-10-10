#include "pch.h"
#include "CoreClient.h"
#include <chrono>
#include <shlobj.h>
#include <winrt/Windows.Storage.Streams.h>
namespace amiebl {
using namespace winrt;
using namespace Windows::Web::Http;
using namespace Windows::Foundation;
std::wstring CoreClient::Option(std::wstring const &name) {
    int n = 0;
    auto argv = CommandLineToArgvW(GetCommandLineW(), &n);
    std::wstring result;
    if (argv) {
        for (int i = 1; i + 1 < n; i++)
            if (argv[i] == name) {
                result = argv[i + 1];
                break;
            }
        LocalFree(argv);
    }
    return result;
}
bool CoreClient::HasFlag(std::wstring const &name) {
    int count = 0;
    auto argv = CommandLineToArgvW(GetCommandLineW(), &count);
    bool found = false;
    if (argv) {
        for (int i = 1; i < count; i++)
            if (argv[i] == name)
                found = true;
        LocalFree(argv);
    }
    return found;
}
CoreClient::CoreClient() {
    auto filter = Windows::Web::Http::Filters::HttpBaseProtocolFilter();
    filter.AllowAutoRedirect(false);
    filter.UseProxy(false);
    // Windows HTTP otherwise caches successful status/config GETs; the C#
    // reference transport always observes the service's current state.
    filter.CacheControl().ReadBehavior(Windows::Web::Http::Filters::HttpCacheReadBehavior::NoCache);
    filter.CacheControl().WriteBehavior(
        Windows::Web::Http::Filters::HttpCacheWriteBehavior::NoCache);
    http = HttpClient(filter);
    auto dir = Option(L"--data-dir");
    if (dir.empty()) {
#ifdef AMIEBL_RELEASE_BUILD
        wchar_t executable[32768];
        GetModuleFileNameW(nullptr, executable, 32768);
        auto app = std::filesystem::path(executable).parent_path();
        if (std::filesystem::exists(app / L"portable.flag") &&
            !std::filesystem::exists(app / L"installed.flag")) {
            dir = (app / L"data").wstring();
        } else {
            wchar_t *local = nullptr;
            check_hresult(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &local));
            dir = (std::filesystem::path(local) / L"LocalModelManager").wstring();
            CoTaskMemFree(local);
        }
#else
        throw hresult_error(E_INVALIDARG, L"原生開發版需指定 --data-dir 隔離資料目錄。");
#endif
    }
    dataDir = std::filesystem::absolute(dir);
    if (std::filesystem::exists(dataDir / L"config.json")) {
        std::ifstream f(dataDir / L"config.json");
        std::string s((std::istreambuf_iterator<char>(f)), {});
        port = static_cast<int>(number(JsonObject::Parse(to_hstring(s)), L"api_port", 8080));
    }
    auto p = Option(L"--port");
    if (!p.empty())
        port = std::stoi(p);
    if (port < 1024 || port > 65535)
        throw hresult_error(E_INVALIDARG, L"Agent 連接埠範圍不正確。");
}
CoreClient::~CoreClient() {
    if (job)
        CloseHandle(job);
    if (process)
        CloseHandle(process);
}
bool CoreClient::OwnedExited() const {
    return process && WaitForSingleObject(process, 0) == WAIT_OBJECT_0;
}
IAsyncOperation<bool> CoreClient::Compatible() {
    HttpResponseMessage response{nullptr};
    try {
        response =
            co_await http.GetAsync(Uri(L"http://127.0.0.1:" + to_hstring(port) + L"/health"));
    } catch (hresult_error const &) {
        co_return false;
    }
    hstring protocol;
    auto health = JsonObject::Parse(co_await response.Content().ReadAsStringAsync());
    if (!response.IsSuccessStatusCode() || str(health, L"app") != L"local-model-manager" ||
        !response.Headers().HasKey(L"X-AMIEBL-Core-Protocol") ||
        response.Headers().Lookup(L"X-AMIEBL-Core-Protocol") != L"1")
        throw hresult_error(E_FAIL, L"連接埠上的服務與此 GUI 不相容（需要 AMIEBL "
                                    L"Core 協定 1）。未停止或修改該服務。");
    co_return true;
}
IAsyncAction CoreClient::Connect() {
    bool found = co_await Compatible();
    if (!found) {
        if (process && !OwnedExited())
            throw hresult_error(E_FAIL, L"背景服務尚未就緒；請檢查連接埠與資料目錄。");
        auto exe = Option(L"--core");
        if (exe.empty()) {
            wchar_t path[32768];
            GetModuleFileNameW(nullptr, path, 32768);
            exe = (std::filesystem::path(path).parent_path() / L"amiebl-core.exe").wstring();
        }
        if (!std::filesystem::is_regular_file(exe))
            throw hresult_error(E_FAIL, L"找不到 amiebl-core.exe；請指定 --core 原生服務路徑。");
        std::filesystem::create_directories(dataDir);
        // Paths cannot contain quotes on Windows; quote each argument, never invoke
        // a shell.
        std::wstring command = L"\"" + exe + L"\" --experimental-runtime --data-dir \"" +
                               dataDir.wstring() + L"\" --port " + std::to_wstring(port);
        auto enginePort = Option(L"--engine-port");
        if (!enginePort.empty())
            command += L" --engine-port " + std::to_wstring(std::stoi(enginePort));
        HANDLE nextJob = CreateJobObjectW(nullptr, nullptr);
        if (!nextJob)
            throw hresult_error(HRESULT_FROM_WIN32(GetLastError()));
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limit{};
        limit.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (!SetInformationJobObject(nextJob, JobObjectExtendedLimitInformation, &limit,
                                     sizeof(limit))) {
            auto e = GetLastError();
            CloseHandle(nextJob);
            throw hresult_error(HRESULT_FROM_WIN32(e));
        }
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION pi{};
        if (!CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, FALSE,
                            CREATE_NO_WINDOW | CREATE_SUSPENDED, nullptr, dataDir.c_str(), &startup,
                            &pi)) {
            auto e = GetLastError();
            CloseHandle(nextJob);
            throw hresult_error(HRESULT_FROM_WIN32(e));
        }
        if (!AssignProcessToJobObject(nextJob, pi.hProcess)) {
            auto e = GetLastError();
            TerminateProcess(pi.hProcess, 1);
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
            CloseHandle(nextJob);
            throw hresult_error(HRESULT_FROM_WIN32(e));
        }
        if (process)
            CloseHandle(process);
        if (job)
            CloseHandle(job);
        process = pi.hProcess;
        job = nextJob;
        ResumeThread(pi.hThread);
        CloseHandle(pi.hThread);
        for (int i = 0; i < 80; i++) {
            if (OwnedExited())
                throw hresult_error(E_FAIL, L"背景服務異常退出，請確認連接埠與設定。");
            if (co_await Compatible()) {
                found = true;
                break;
            }
            co_await resume_after(std::chrono::milliseconds(250));
        }
        if (!found)
            throw hresult_error(E_FAIL, L"背景服務啟動超時。");
    }
    std::ifstream f(dataDir / L"admin-token");
    std::string s((std::istreambuf_iterator<char>(f)), {});
    while (!s.empty() && isspace(static_cast<unsigned char>(s.back())))
        s.pop_back();
    if (s.empty())
        throw hresult_error(E_FAIL, L"此資料夾沒有相符的管理存取憑證。未停止既有服務。");
    token = to_hstring(s);
    co_await Request(L"GET", L"/manager/config");
}
IAsyncOperation<JsonObject> CoreClient::Request(hstring method, hstring path, JsonObject payload) {
    if (path.empty() || path[0] != L'/')
        throw hresult_error(E_INVALIDARG);
    HttpRequestMessage request(HttpMethod(method),
                               Uri(L"http://127.0.0.1:" + to_hstring(port) + path));
    request.Headers().Append(L"X-Manager-Token", token);
    if (method != L"GET")
        request.Content(HttpStringContent(payload.Stringify(),
                                          Windows::Storage::Streams::UnicodeEncoding::Utf8,
                                          L"application/json"));
    auto response = co_await http.SendRequestAsync(request);
    auto text = co_await response.Content().ReadAsStringAsync();
    JsonObject parsed;
    if (!JsonObject::TryParse(text, parsed))
        throw hresult_error(E_FAIL, L"背景服務回應不是有效 JSON。");
    if (!response.IsSuccessStatusCode())
        throw hresult_error(E_FAIL,
                            str(parsed, L"detail",
                                str(object(parsed, L"error"), L"message", L"背景服務拒絕操作。")));
    co_return parsed;
}
IAsyncAction CoreClient::Shutdown() {
    if (process && !OwnedExited()) {
        try {
            co_await Request(L"POST", L"/manager/shutdown");
        } catch (hresult_error const &) {
        }
        for (int i = 0; i < 100 && !OwnedExited(); i++)
            co_await resume_after(std::chrono::milliseconds(100));
        // Closing our job kills only children explicitly assigned at launch.
        if (job) {
            CloseHandle(job);
            job = nullptr;
        }
    }
}
} // namespace amiebl
