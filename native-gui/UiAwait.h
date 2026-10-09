#pragma once
#include <winrt/Microsoft.UI.Dispatching.h>
namespace amiebl {
struct ResumeUI {
    winrt::Microsoft::UI::Dispatching::DispatcherQueue queue;
    bool await_ready() const noexcept { return false; }
    void await_suspend(std::coroutine_handle<> handle) const {
        if (!queue.TryEnqueue([handle] { handle.resume(); }))
            throw winrt::hresult_error(RPC_E_DISCONNECTED);
    }
    void await_resume() const noexcept {}
};
} // namespace amiebl
