#pragma once
#include "MainWindow.g.h"
namespace winrt::AMIEBL::Native::implementation {
struct MainWindow : MainWindowT<MainWindow> {
    MainWindow();
    void Navigate(Windows::Foundation::IInspectable const&, Microsoft::UI::Xaml::Controls::NavigationViewSelectionChangedEventArgs const&);
    void ExitClicked(Windows::Foundation::IInspectable const&, Microsoft::UI::Xaml::RoutedEventArgs const&);
};
}
namespace winrt::AMIEBL::Native::factory_implementation {
struct MainWindow : MainWindowT<MainWindow, implementation::MainWindow> {};
}
