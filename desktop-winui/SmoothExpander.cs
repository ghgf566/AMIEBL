using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Automation;
using Windows.Foundation;

namespace AMIEBL.WinUI;

// Animate the layout height, so neighboring rows and scroll extents move too.
public sealed class SmoothExpander : UserControl
{
    private readonly Grid layout=new();
    private readonly ToggleButton headerButton=new() { HorizontalContentAlignment=HorizontalAlignment.Stretch,Height=48 };
    private readonly TextBlock label=new() { VerticalAlignment=VerticalAlignment.Center };
    private readonly FontIcon arrow=new() { FontSize=12,HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Center };
    private readonly Canvas viewport=new() { Height=0,Visibility=Visibility.Collapsed };
    private readonly ContentPresenter presenter=new() { Padding=new Thickness(16),HorizontalContentAlignment=HorizontalAlignment.Stretch };
    private readonly RectangleGeometry clip=new();
    private readonly DispatcherTimer timer=new() { Interval=TimeSpan.FromMilliseconds(16) };
    private readonly Stopwatch clock=new();
    private readonly Windows.UI.ViewManagement.UISettings settings=new();
    private double from,to;
    private bool expanded;
    private ExpandDirection direction;
    public event EventHandler? Expanding;
    public event EventHandler? ProgressChanged;
    public event EventHandler? AnimationCompleted;
    public double AnimationProgress { get; private set; }
    public double ContentHeight=>viewport.Height;
    public object Header { get=>label.Text; set {label.Text=value?.ToString()??"";AutomationProperties.SetName(headerButton,label.Text);} }
    public new UIElement? Content { get=>presenter.Content as UIElement; set=>presenter.Content=value; }
    public ExpandDirection ExpandDirection { get=>direction; set {direction=value;PlaceRows();} }
    public bool IsExpanded
    {
        get=>expanded;
        set
        {
            if(expanded==value)return;
            expanded=value;headerButton.IsChecked=value;arrow.Glyph=value?"\uE70E":"\uE70D";
            if(value)viewport.Visibility=Visibility.Visible;
            double width=Math.Max(1,ActualWidth);
            presenter.Width=width;
            presenter.MaxHeight=double.IsFinite(MaxHeight)?Math.Max(0,MaxHeight-headerButton.Height):double.PositiveInfinity;
            presenter.Measure(new Size(width,double.PositiveInfinity));
            from=viewport.Height;to=value?presenter.DesiredSize.Height:0;
            AnimationProgress=0;
            if(value)Expanding?.Invoke(this,EventArgs.Empty);
            timer.Stop();clock.Restart();
            if(settings.AnimationsEnabled&&Math.Abs(from-to)>0.5)timer.Start();
            else Finish();
        }
    }
    public SmoothExpander()
    {
        layout.RowDefinitions.Add(new(){Height=GridLength.Auto});layout.RowDefinitions.Add(new(){Height=GridLength.Auto});
        var title=new Grid();title.Children.Add(label);title.Children.Add(arrow);headerButton.Content=title;
        headerButton.Checked+=(_,_)=>IsExpanded=true;headerButton.Unchecked+=(_,_)=>IsExpanded=false;
        viewport.Background=new SolidColorBrush(Windows.UI.Color.FromArgb(255,37,43,53));viewport.Clip=clip;viewport.Children.Add(presenter);
        layout.Children.Add(headerButton);layout.Children.Add(viewport);base.Content=layout;PlaceRows();arrow.Glyph="\uE70D";
        timer.Tick+=(_,_)=>
        {
            double t=Math.Clamp(clock.Elapsed.TotalMilliseconds/260,0,1);
            AnimationProgress=t*t*(3-2*t);viewport.Height=from+(to-from)*AnimationProgress;
            ProgressChanged?.Invoke(this,EventArgs.Empty);
            if(t>=1)Finish();
        };
        viewport.SizeChanged+=(_,e)=>clip.Rect=new Rect(0,0,e.NewSize.Width,e.NewSize.Height);
        SizeChanged+=(_,e)=> {if(Math.Abs(e.NewSize.Width-e.PreviousSize.Width)>0.5) {presenter.Width=Math.Max(1,e.NewSize.Width);if(expanded){presenter.Measure(new Size(presenter.Width,double.PositiveInfinity));to=presenter.DesiredSize.Height;if(!timer.IsEnabled)viewport.Height=to;}}};
        presenter.SizeChanged+=(_,e)=>
        {
            if(!expanded||Math.Abs(e.NewSize.Height-to)<0.5)return;
            to=e.NewSize.Height;if(!timer.IsEnabled)viewport.Height=to;
        };
        Unloaded+=(_,_)=>timer.Stop();
    }
    private void PlaceRows() {Grid.SetRow(headerButton,direction==ExpandDirection.Up?1:0);Grid.SetRow(viewport,direction==ExpandDirection.Up?0:1);}
    private void Finish()
    {
        timer.Stop();viewport.Height=to;AnimationProgress=1;
        if(!expanded)viewport.Visibility=Visibility.Collapsed;
        ProgressChanged?.Invoke(this,EventArgs.Empty);AnimationCompleted?.Invoke(this,EventArgs.Empty);
    }
}
