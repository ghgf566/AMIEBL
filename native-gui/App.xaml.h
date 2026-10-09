#pragma once
#include "App.xaml.g.h"
namespace winrt::AMIEBL::Native::implementation {
struct App : AppT<App> {
    App();
    void OnLaunched(Microsoft::UI::Xaml::LaunchActivatedEventArgs const&);
private:
    Microsoft::UI::Xaml::Window window{nullptr};
    Microsoft::UI::Dispatching::DispatcherQueueTimer smoke_timer{nullptr};
};
}
