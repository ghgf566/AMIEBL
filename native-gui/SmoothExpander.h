#pragma once
#include <winrt/Windows.UI.ViewManagement.h>
#include <chrono>
#include <memory>
#include <functional>
#include <cmath>
namespace amiebl {
// Direct layout-animation port of frozen SmoothExpander.cs. The native SDK
// header remains authoritative for input, focus, visual states and automation.
struct SmoothExpander : std::enable_shared_from_this<SmoothExpander> {
    winrt::Microsoft::UI::Xaml::Controls::UserControl root;
    winrt::Microsoft::UI::Xaml::Controls::Expander native;
    winrt::Microsoft::UI::Xaml::Controls::Border viewport{nullptr}, body{nullptr};
    winrt::Microsoft::UI::Xaml::Controls::Primitives::ToggleButton header{nullptr};
    winrt::event_token renderToken{};
    bool animating = false, measuring = false;
    double from = 0, to = 0, progress = 0, lastMs = 0, maxGap = 0;
    int frames = 0, lastFrames = 0;
    std::chrono::steady_clock::time_point started;
    std::function<void()> changed, completed;
    static std::shared_ptr<SmoothExpander>
    Create(winrt::hstring label, winrt::Microsoft::UI::Xaml::UIElement content,
           winrt::Microsoft::UI::Xaml::Controls::ExpandDirection direction =
               winrt::Microsoft::UI::Xaml::Controls::ExpandDirection::Down) {
        using namespace winrt::Microsoft::UI::Xaml;
        auto s = std::make_shared<SmoothExpander>();
        s->native.Header(winrt::box_value(label));
        s->native.Content(content);
        s->native.ExpandDirection(direction);
        s->native.HorizontalAlignment(HorizontalAlignment::Stretch);
        s->native.HorizontalContentAlignment(HorizontalAlignment::Stretch);
        s->root.Content(s->native);
        s->root.HorizontalAlignment(HorizontalAlignment::Stretch);
        s->root.HorizontalContentAlignment(HorizontalAlignment::Stretch);
        std::weak_ptr<SmoothExpander> weak = s;
        s->root.Loaded([weak](auto const &, auto const &) {
            if (auto p = weak.lock())
                p->Prepare();
        });
        s->root.Unloaded([weak](auto const &, auto const &) {
            if (auto p = weak.lock())
                p->Stop();
        });
        s->native.RegisterPropertyChangedCallback(Controls::Expander::IsExpandedProperty(),
                                                  [weak](auto const &, auto const &) {
                                                      if (auto p = weak.lock())
                                                          p->Animate();
                                                  });
        s->root.SizeChanged([weak](auto const &, SizeChangedEventArgs const &e) {
            if (auto p = weak.lock())
                if (p->body && p->native.IsExpanded() &&
                    std::abs(e.NewSize().Width - e.PreviousSize().Width) >= 0.5) {
                    p->Measure();
                    if (!p->animating)
                        p->viewport.Height(p->to);
                }
        });
        content.as<FrameworkElement>().SizeChanged([weak](auto const &, auto const &) {
            if (auto p = weak.lock())
                if (!p->measuring && p->body && p->native.IsExpanded()) {
                    p->Measure();
                    if (!p->animating)
                        p->viewport.Height(p->to);
                }
        });
        return s;
    }
    ~SmoothExpander() { Stop(); }
    void Stop() {
        if (animating) {
            winrt::Microsoft::UI::Xaml::Media::CompositionTarget::Rendering(renderToken);
            animating = false;
        }
    }
    double Height() const {
        return viewport && std::isfinite(viewport.Height()) ? viewport.Height() : 0;
    }
    static winrt::Microsoft::UI::Xaml::FrameworkElement
    Find(winrt::Microsoft::UI::Xaml::DependencyObject root, winrt::hstring name) {
        using namespace winrt::Microsoft::UI::Xaml;
        using namespace Media;
        if (auto e = root.try_as<FrameworkElement>())
            if (e.Name() == name)
                return e;
        for (int i = 0; i < VisualTreeHelper::GetChildrenCount(root); i++) {
            auto e = Find(VisualTreeHelper::GetChild(root, i), name);
            if (e)
                return e;
        }
        return nullptr;
    }
    void Corners() {
        using namespace winrt::Microsoft::UI::Xaml::Controls;
        auto r = native.CornerRadius();
        bool up = native.ExpandDirection() == ExpandDirection::Up;
        header.CornerRadius(
            !native.IsExpanded() ? r
            : up ? winrt::Microsoft::UI::Xaml::CornerRadius{0, 0, r.BottomRight, r.BottomLeft}
                 : winrt::Microsoft::UI::Xaml::CornerRadius{r.TopLeft, r.TopRight, 0, 0});
        body.CornerRadius(
            up ? winrt::Microsoft::UI::Xaml::CornerRadius{r.TopLeft, r.TopRight, 0, 0}
               : winrt::Microsoft::UI::Xaml::CornerRadius{0, 0, r.BottomRight, r.BottomLeft});
    }
    void Prepare() {
        using namespace winrt::Microsoft::UI::Xaml;
        using namespace Controls;
        if (viewport)
            return;
        native.ApplyTemplate();
        header = Find(native, L"ExpanderHeader").try_as<Primitives::ToggleButton>();
        viewport = Find(native, L"ExpanderContentClip").try_as<Border>();
        body = Find(native, L"ExpanderContent").try_as<Border>();
        if (!header || !header.Style() || !viewport || !body)
            throw winrt::hresult_error(E_FAIL,
                                       L"WinUI Expander 樣板結構已變更，請更新版面動畫介接。");
        auto style = header.Style();
        auto background = header.Background();
        auto border = header.BorderBrush();
        auto thickness = header.BorderThickness();
        auto bodyBackground = body.Background();
        auto bodyBorder = body.BorderBrush();
        auto padding = body.Padding();
        native.Template(
            Markup::XamlReader::Load(
                LR"(<ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="Expander"><Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions><ToggleButton x:Name="ExpanderHeader" MinHeight="48" HorizontalAlignment="Stretch" HorizontalContentAlignment="Stretch" Content="{TemplateBinding Header}" IsChecked="{Binding IsExpanded,Mode=TwoWay,RelativeSource={RelativeSource TemplatedParent}}"/><Border x:Name="ExpanderContentClip" Grid.Row="1"><Border x:Name="ExpanderContent"><ContentPresenter Content="{TemplateBinding Content}" HorizontalContentAlignment="Stretch"/></Border></Border></Grid></ControlTemplate>)")
                .as<ControlTemplate>());
        native.ApplyTemplate();
        header = Find(native, L"ExpanderHeader").as<Primitives::ToggleButton>();
        viewport = Find(native, L"ExpanderContentClip").as<Border>();
        body = Find(native, L"ExpanderContent").as<Border>();
        header.Style(style);
        header.Background(background);
        header.BorderBrush(border);
        header.BorderThickness(thickness);
        body.Background(bodyBackground);
        body.BorderBrush(bodyBorder);
        body.Padding(padding);
        body.BorderThickness({1, 1, 1, 1});
        bool up = native.ExpandDirection() == ExpandDirection::Up;
        Grid::SetRow(header, up ? 1 : 0);
        Grid::SetRow(viewport, up ? 0 : 1);
        Corners();
        Canvas surface;
        viewport.Child(nullptr);
        surface.Children().Append(body);
        viewport.Child(surface);
        Media::RectangleGeometry clip;
        viewport.Clip(clip);
        std::weak_ptr<SmoothExpander> weak = shared_from_this();
        viewport.SizeChanged([weak, clip](auto const &, SizeChangedEventArgs const &e) {
            clip.Rect({0, 0, e.NewSize().Width, e.NewSize().Height});
            if (auto p = weak.lock())
                Hosting::ElementCompositionPreview::GetElementVisual(p->viewport).Clip(nullptr);
        });
        viewport.VerticalAlignment(VerticalAlignment::Top);
        viewport.Height(0);
        viewport.Visibility(native.IsExpanded() ? Visibility::Visible : Visibility::Collapsed);
        body.SizeChanged([weak](auto const &, SizeChangedEventArgs const &e) {
            if (auto p = weak.lock())
                if (p->native.IsExpanded() && std::abs(e.NewSize().Height - p->to) >= 0.5) {
                    p->to = e.NewSize().Height;
                    if (!p->animating)
                        p->viewport.Height(p->to);
                }
        });
        if (native.IsExpanded()) {
            Measure();
            viewport.Height(to);
        }
    }
    void Measure() {
        using namespace winrt::Microsoft::UI::Xaml;
        measuring = true;
        body.Width(std::max(1.0, root.ActualWidth()));
        body.MaxHeight(std::isfinite(root.MaxHeight())
                           ? std::max(0.0, root.MaxHeight() - (header ? header.ActualHeight() : 48))
                           : INFINITY);
        body.Height(NAN);
        body.Measure({static_cast<float>(body.Width()), INFINITY});
        to = body.DesiredSize().Height;
        body.Height(to);
        viewport.Width(body.Width());
        if (auto c = viewport.Child().try_as<Controls::Canvas>()) {
            c.Width(body.Width());
            c.Height(to);
        }
        measuring = false;
    }
    void Animate() {
        using namespace winrt::Microsoft::UI::Xaml;
        if (!viewport || !body)
            return;
        Corners();
        viewport.Visibility(Visibility::Visible);
        body.Visibility(Visibility::Visible);
        from = Height();
        Measure();
        if (!native.IsExpanded())
            to = 0;
        progress = 0;
        Stop();
        started = std::chrono::steady_clock::now();
        if (winrt::Windows::UI::ViewManagement::UISettings().AnimationsEnabled() &&
            std::abs(from - to) > 0.5) {
            animating = true;
            frames = 0;
            lastMs = 0;
            maxGap = 0;
            std::weak_ptr<SmoothExpander> weak = shared_from_this();
            renderToken = Media::CompositionTarget::Rendering([weak](auto const &, auto const &) {
                if (auto p = weak.lock())
                    p->Frame();
            });
        } else
            Finish();
    }
    void Frame() {
        double ms =
            std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started)
                .count();
        if (frames > 0)
            maxGap = std::max(maxGap, ms - lastMs);
        lastMs = ms;
        frames++;
        double t = std::clamp(ms / 260.0, 0.0, 1.0);
        progress = t * t * (3 - 2 * t);
        viewport.Height(from + (to - from) * progress);
        if (changed)
            changed();
        if (t >= 1)
            Finish();
    }
    void Finish() {
        using namespace winrt::Microsoft::UI::Xaml;
        lastFrames = frames;
        Stop();
        viewport.Height(to);
        progress = 1;
        viewport.Visibility(native.IsExpanded() ? Visibility::Visible : Visibility::Collapsed);
        if (changed)
            changed();
        if (completed)
            completed();
    }
};
} // namespace amiebl
