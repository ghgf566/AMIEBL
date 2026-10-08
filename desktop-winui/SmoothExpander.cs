using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Windows.Foundation;

namespace AMIEBL.WinUI;

// Use a native Expander and its SDK header style with a layout-animation template.
public sealed class SmoothExpander : UserControl
{
    private readonly Expander native=new() { HorizontalAlignment=HorizontalAlignment.Stretch,HorizontalContentAlignment=HorizontalAlignment.Stretch };
    private Border? viewport, body;
    private ToggleButton? header;
    private readonly DispatcherTimer timer=new() { Interval=TimeSpan.FromMilliseconds(16) };
    private readonly Stopwatch clock=new();
    private readonly Windows.UI.ViewManagement.UISettings settings=new();
    private double from,to;
    public object Header { get=>native.Header; set=>native.Header=value; }
    public new UIElement? Content
    {
        get=>native.Content as UIElement;
        set
        {
            if(native.Content is FrameworkElement previous)previous.SizeChanged-=ContentSizeChanged;
            native.Content=value;
            if(value is FrameworkElement next)next.SizeChanged+=ContentSizeChanged;
        }
    }
    private bool measuring;
    private void ContentSizeChanged(object sender,SizeChangedEventArgs args)
    {
        if(measuring||body is null||!IsExpanded)return;
        MeasureBody();if(!timer.IsEnabled)viewport!.Height=to;
    }
    public ExpandDirection ExpandDirection { get=>native.ExpandDirection; set=>native.ExpandDirection=value; }
    public bool IsExpanded { get=>native.IsExpanded; set=>native.IsExpanded=value; }
    public event EventHandler? Expanding;
    public event EventHandler? ProgressChanged;
    public event EventHandler? AnimationCompleted;
    public double AnimationProgress { get; private set; }
    public double ContentHeight=>viewport?.Height is double height && double.IsFinite(height)?height:0;
    public bool NativeTemplateVerified=>header?.Style is not null && body is not null && viewport is not null;

    public SmoothExpander()
    {
        base.Content=native;
        native.Expanding+=(_,_)=>Expanding?.Invoke(this,EventArgs.Empty);
        Loaded+=(_,_)=>PrepareTemplate();
        native.RegisterPropertyChangedCallback(Expander.IsExpandedProperty,(_,_)=>Animate());
        timer.Tick+=(_,_)=>
        {
            if(viewport is null)return;
            double t=Math.Clamp(clock.Elapsed.TotalMilliseconds/260,0,1);
            AnimationProgress=t*t*(3-2*t);viewport.Height=from+(to-from)*AnimationProgress;
            ProgressChanged?.Invoke(this,EventArgs.Empty);
            if(t>=1)Finish();
        };
        SizeChanged+=(_,e)=>
        {
            if(body is null||!IsExpanded||Math.Abs(e.NewSize.Width-e.PreviousSize.Width)<0.5)return;
            MeasureBody();if(!timer.IsEnabled)viewport!.Height=to;
        };
        Unloaded+=(_,_)=>timer.Stop();
    }
    private void PrepareTemplate()
    {
        if(viewport is not null)return;
        timer.Stop();native.ApplyTemplate();
        header=Find(native,"ExpanderHeader") as ToggleButton;
        viewport=Find(native,"ExpanderContentClip") as Border;
        body=Find(native,"ExpanderContent") as Border;
        if(!NativeTemplateVerified)throw new InvalidOperationException("WinUI Expander 樣板結構已變更，請更新版面動畫介接。");
        var headerStyle=header!.Style;var headerBackground=header.Background;var headerBorder=header.BorderBrush;var headerThickness=header.BorderThickness;
        var bodyBackground=body!.Background;var bodyBorder=body.BorderBrush;var bodyPadding=body.Padding;
        // Preserve the SDK header style; give the native control a layout-only shell.
        native.Template=(ControlTemplate)Microsoft.UI.Xaml.Markup.XamlReader.Load("""
            <ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="Expander">
              <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
                <ToggleButton x:Name="ExpanderHeader" MinHeight="48" HorizontalAlignment="Stretch" HorizontalContentAlignment="Stretch" Content="{TemplateBinding Header}" IsChecked="{Binding IsExpanded,Mode=TwoWay,RelativeSource={RelativeSource TemplatedParent}}"/>
                <Border x:Name="ExpanderContentClip" Grid.Row="1"><Border x:Name="ExpanderContent"><ContentPresenter Content="{TemplateBinding Content}" HorizontalContentAlignment="Stretch"/></Border></Border>
              </Grid>
            </ControlTemplate>
            """);
        native.ApplyTemplate();
        header=(ToggleButton)Find(native,"ExpanderHeader")!;viewport=(Border)Find(native,"ExpanderContentClip")!;body=(Border)Find(native,"ExpanderContent")!;
        header.Style=headerStyle;header.Background=headerBackground;header.BorderBrush=headerBorder;header.BorderThickness=headerThickness;
        body.Background=bodyBackground;body.BorderBrush=bodyBorder;body.Padding=bodyPadding;body.BorderThickness=new Thickness(1);
        Grid.SetRow(header,ExpandDirection==ExpandDirection.Up?1:0);Grid.SetRow(viewport,ExpandDirection==ExpandDirection.Up?0:1);
        SetCorners();
        // Canvas measures the native body independently of the animated viewport.
        // This keeps the target height stable while the viewport is partially open.
        var surface=new Canvas();viewport!.Child=null;surface.Children.Add(body);viewport.Child=surface;
        var layoutClip=new Microsoft.UI.Xaml.Media.RectangleGeometry();viewport.Clip=layoutClip;
        viewport.SizeChanged+=(_,e)=> {layoutClip.Rect=new Rect(0,0,e.NewSize.Width,e.NewSize.Height);Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementVisual(viewport).Clip=null;};
        viewport.VerticalAlignment=VerticalAlignment.Top;
        viewport.Height=0;viewport.Visibility=IsExpanded?Visibility.Visible:Visibility.Collapsed;
        body.SizeChanged+=(_,e)=>
        {
            if(!IsExpanded||Math.Abs(e.NewSize.Height-to)<0.5)return;
            to=e.NewSize.Height;if(!timer.IsEnabled)viewport.Height=to;
        };
        if(IsExpanded){MeasureBody();viewport.Height=to;}
    }
    private static FrameworkElement? Find(DependencyObject root,string name)
    {
        if(root is FrameworkElement element&&element.Name==name)return element;
        for(int i=0;i<Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChildrenCount(root);i++){var found=Find(Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChild(root,i),name);if(found is not null)return found;}
        return null;
    }
    private void SetCorners()
    {
        var r=native.CornerRadius;
        header!.CornerRadius=!IsExpanded?r:ExpandDirection==ExpandDirection.Up?new CornerRadius(0,0,r.BottomRight,r.BottomLeft):new CornerRadius(r.TopLeft,r.TopRight,0,0);
        body!.CornerRadius=ExpandDirection==ExpandDirection.Up?new CornerRadius(r.TopLeft,r.TopRight,0,0):new CornerRadius(0,0,r.BottomRight,r.BottomLeft);
    }
    private void MeasureBody()
    {
        measuring=true;
        try {
        body!.Width=Math.Max(1,ActualWidth);
        body.MaxHeight=double.IsFinite(MaxHeight)?Math.Max(0,MaxHeight-(header?.ActualHeight??48)):double.PositiveInfinity;
        body.Height=double.NaN;body.Measure(new Size(body.Width,double.PositiveInfinity));to=body.DesiredSize.Height;
        body.Height=to;viewport!.Width=body.Width;
        if(viewport.Child is Canvas surface){surface.Width=body.Width;surface.Height=to;}
        } finally {measuring=false;}
    }
    private void Animate()
    {
        if(viewport is null||body is null)return;
        SetCorners();viewport.Visibility=Visibility.Visible;body.Visibility=Visibility.Visible;
        from=ContentHeight;MeasureBody();if(!IsExpanded)to=0;
        AnimationProgress=0;timer.Stop();clock.Restart();
        if(settings.AnimationsEnabled&&Math.Abs(from-to)>0.5)timer.Start();else Finish();
    }
    private void Finish()
    {
        timer.Stop();viewport!.Height=to;AnimationProgress=1;
        if(IsExpanded){body!.Visibility=Visibility.Visible;if(body.RenderTransform is Microsoft.UI.Xaml.Media.CompositeTransform transform)transform.TranslateY=0;Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementVisual(viewport).Clip=null;}
        if(!IsExpanded)viewport.Visibility=Visibility.Collapsed;
        ProgressChanged?.Invoke(this,EventArgs.Empty);AnimationCompleted?.Invoke(this,EventArgs.Empty);
    }
}
