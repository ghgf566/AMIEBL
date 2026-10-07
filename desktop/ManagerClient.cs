using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace LocalModelManager;

public sealed class ManagerClient : IDisposable
{
    private readonly HttpClient client;
    public string BaseUrl { get; }
    public ManagerClient(int port, string token)
    {
        BaseUrl = $"http://127.0.0.1:{port}";
        client = new HttpClient { BaseAddress = new Uri(BaseUrl), Timeout = TimeSpan.FromSeconds(30) };
        client.DefaultRequestHeaders.Add("X-Manager-Token", token);
    }
    public async Task<JsonObject> Get(string path) => await Send(HttpMethod.Get, path, null);
    public async Task<JsonObject> Post(string path, JsonObject? body = null) => await Send(HttpMethod.Post, path, body ?? new JsonObject());
    public async Task<JsonObject> Put(string path, JsonObject body) => await Send(HttpMethod.Put, path, body);
    private async Task<JsonObject> Send(HttpMethod method, string path, JsonObject? body)
    {
        using var request = new HttpRequestMessage(method, path);
        if (body is not null) request.Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await client.SendAsync(request);
        string text = await response.Content.ReadAsStringAsync();
        JsonObject data;
        try { data = JsonNode.Parse(text)?.AsObject() ?? new JsonObject(); }
        catch { throw new InvalidOperationException($"服務回應格式無法辨識（HTTP {(int)response.StatusCode}）。"); }
        if (!response.IsSuccessStatusCode)
        {
            var detail = data["detail"] ?? data["error"];
            throw new InvalidOperationException(detail?.ToString() ?? $"服務回報 HTTP {(int)response.StatusCode}。");
        }
        return data;
    }
    public static async Task<bool> IsManager(int port, CancellationToken cancel = default)
    {
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
        try
        {
            var json = JsonNode.Parse(await http.GetStringAsync($"http://127.0.0.1:{port}/health", cancel));
            return json?["app"]?.ToString() == "local-model-manager";
        }
        catch { return false; }
    }
    public void Dispose() => client.Dispose();
}

public static class J
{
    public static string S(JsonNode? node, string key, string fallback = "") => node?[key]?.ToString() ?? fallback;
    public static bool B(JsonNode? node, string key, bool fallback = false) => bool.TryParse(S(node, key), out var value) ? value : fallback;
    public static int I(JsonNode? node, string key, int fallback = 0) => int.TryParse(S(node, key), out var value) ? value : fallback;
    public static double? D(JsonNode? node, string key) => double.TryParse(S(node, key), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var value) ? value : null;
    public static JsonArray A(JsonNode? node, string key) => node?[key] as JsonArray ?? new JsonArray();
    public static string Metric(JsonNode? node, string key, string suffix = "", string format = "0.0") => D(node, key) is double value ? value.ToString(format) + suffix : "未提供";
}
