#include "pch.h"
#include "MainWindow.xaml.h"
#if __has_include("MainWindow.g.cpp")
#include "MainWindow.g.cpp"
#endif
namespace winrt::AMIEBL::Native::implementation {
MainWindow::MainWindow() {
    InitializeComponent();
    VersionText().Text(L"v1.1.0 migration — UI parity pending");
    Navigation().SelectedItem(Navigation().MenuItems().GetAt(0));
}
void MainWindow::Navigate(Windows::Foundation::IInspectable const&, Microsoft::UI::Xaml::Controls::NavigationViewSelectionChangedEventArgs const& args) {
    if (auto item = args.SelectedItem().try_as<Microsoft::UI::Xaml::Controls::NavigationViewItem>()) {
        auto page = winrt::unbox_value<winrt::hstring>(item.Tag());
        TitleText().Text(page);
        ExitButton().Visibility(page == L"系統" ? Microsoft::UI::Xaml::Visibility::Visible : Microsoft::UI::Xaml::Visibility::Collapsed);
    }
}
void MainWindow::ExitClicked(Windows::Foundation::IInspectable const&, Microsoft::UI::Xaml::RoutedEventArgs const&) { Close(); }
}
