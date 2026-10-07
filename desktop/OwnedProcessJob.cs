using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace LocalModelManager;

/// <summary>Keeps only this desktop's child process tree inside a kill-on-close job.</summary>
internal sealed class OwnedProcessJob : IDisposable
{
    private readonly SafeFileHandle handle;
    private OwnedProcessJob(SafeFileHandle handle) => this.handle = handle;
    internal static OwnedProcessJob? TryCreate(Process process, out string? error)
    {
        error = null;
        var handle = CreateJobObject(IntPtr.Zero, null);
        if (handle.IsInvalid) { error = new Win32Exception(Marshal.GetLastWin32Error()).Message; handle.Dispose(); return null; }
        var limits = new ExtendedLimits { BasicLimitInformation = new BasicLimits { LimitFlags = 0x2000 } }; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf<ExtendedLimits>()) || !AssignProcessToJobObject(handle, process.Handle))
        {
            error = new Win32Exception(Marshal.GetLastWin32Error()).Message; handle.Dispose(); return null;
        }
        return new OwnedProcessJob(handle);
    }
    public void Dispose() => handle.Dispose();
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimits
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits
    {
        public BasicLimits BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateJobObject(IntPtr attributes, string? name);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool SetInformationJobObject(SafeFileHandle job, int informationClass, ref ExtendedLimits information, uint length);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);
}
