// WASAPI output loopback. PCM stays in memory; only visual features are written.
// https://learn.microsoft.com/windows/win32/coreaudio/loopback-recording
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace SodaReactive
{
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class DeviceEnumerator { }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator
    {
        [PreserveSig] int EnumAudioEndpoints(int flow, int mask, out IntPtr devices);
        [PreserveSig] int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice device);
        [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
        [PreserveSig] int RegisterEndpointNotificationCallback(IntPtr callback);
        [PreserveSig] int UnregisterEndpointNotificationCallback(IntPtr callback);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice
    {
        [PreserveSig] int Activate(ref Guid iid, int context, IntPtr parameters,
                                  [MarshalAs(UnmanagedType.IUnknown)] out object iface);
        [PreserveSig] int OpenPropertyStore(int access, out IntPtr properties);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetState(out int state);
    }

    [Guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioClient
    {
        [PreserveSig] int Initialize(int mode, uint flags, long duration, long periodicity,
                                    IntPtr format, ref Guid session);
        [PreserveSig] int GetBufferSize(out uint frames);
        [PreserveSig] int GetStreamLatency(out long latency);
        [PreserveSig] int GetCurrentPadding(out uint frames);
        [PreserveSig] int IsFormatSupported(int mode, IntPtr format, out IntPtr closest);
        [PreserveSig] int GetMixFormat(out IntPtr format);
        [PreserveSig] int GetDevicePeriod(out long normal, out long minimum);
        [PreserveSig] int Start();
        [PreserveSig] int Stop();
        [PreserveSig] int Reset();
        [PreserveSig] int SetEventHandle(IntPtr handle);
        [PreserveSig] int GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    }

    [Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioCaptureClient
    {
        [PreserveSig] int GetBuffer(out IntPtr data, out uint frames, out uint flags,
                                  out ulong position, out ulong counter);
        [PreserveSig] int ReleaseBuffer(uint frames);
        [PreserveSig] int GetNextPacketSize(out uint frames);
    }

    public class SpectrumFrame
    {
        public double[] Bands = new double[48];
        public double[] Wave = new double[128];
        public double Rms, Peak, Bass, Mid, Treble, BassEnergy;
    }

    public static class SpectrumMath
    {
        public const int Size = 4096;
        static readonly double[] Window = MakeWindow();
        static double[] MakeWindow()
        {
            double[] window = new double[Size];
            for (int i = 0; i < Size; i++) window[i] = .5 - .5 * Math.Cos(2 * Math.PI * i / (Size - 1));
            return window;
        }

        static double Level(double amplitude)
        {
            if (amplitude <= .00001) return 0;
            return Math.Max(0, Math.Min(1, (20 * Math.Log10(amplitude) + 72) / 60));
        }

        public static SpectrumFrame Analyze(double[] samples, int rate)
        {
            if (samples.Length != Size || rate <= 0) throw new ArgumentException("Invalid audio frame");
            SpectrumFrame frame = new SpectrumFrame();
            double[] re = new double[Size], im = new double[Size];
            double sum = 0;
            for (int i = 0; i < Size; i++)
            {
                double value = Double.IsNaN(samples[i]) || Double.IsInfinity(samples[i]) ? 0 : samples[i];
                re[i] = value * Window[i];
                sum += value * value;
                frame.Peak = Math.Max(frame.Peak, Math.Abs(value));
            }
            frame.Rms = Math.Sqrt(sum / Size);
            for (int i = 0; i < frame.Wave.Length; i++) frame.Wave[i] = samples[Size - 512 + i * 4];
            if (frame.Rms < .00001) return frame;
            // Iterative radix-2 FFT; the Hann window suppresses leakage between bands.
            for (int i = 1, j = 0; i < Size; i++)
            {
                int bit = Size >> 1;
                for (; (j & bit) != 0; bit >>= 1) j ^= bit;
                j ^= bit;
                if (i < j) { double tmp = re[i]; re[i] = re[j]; re[j] = tmp; }
            }
            for (int length = 2; length <= Size; length <<= 1)
            {
                double angle = -2 * Math.PI / length, wr = Math.Cos(angle), wi = Math.Sin(angle);
                for (int start = 0; start < Size; start += length)
                {
                    double cr = 1, ci = 0;
                    for (int j = 0; j < length / 2; j++)
                    {
                        int a = start + j, b = a + length / 2;
                        double tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
                        re[b] = re[a] - tr; im[b] = im[a] - ti;
                        re[a] += tr; im[a] += ti;
                        double next = cr * wr - ci * wi;
                        ci = cr * wi + ci * wr; cr = next;
                    }
                }
            }
            double[] magnitudes = new double[Size / 2];
            double bassPower = 0;
            for (int i = 1; i < magnitudes.Length; i++)
            {
                magnitudes[i] = 4 * Math.Sqrt(re[i] * re[i] + im[i] * im[i]) / Size;
                double frequency = (double)i * rate / Size;
                if (frequency >= 40 && frequency <= 200) bassPower += magnitudes[i] * magnitudes[i];
            }
            frame.BassEnergy = Math.Sqrt(bassPower);
            double maxFrequency = Math.Min(16000, rate / 2.0 - rate / (double)Size);
            for (int band = 0; band < frame.Bands.Length; band++)
            {
                double low = 40 * Math.Pow(maxFrequency / 40, band / 48.0);
                double high = 40 * Math.Pow(maxFrequency / 40, (band + 1) / 48.0);
                int first = Math.Max(1, (int)Math.Ceiling(low * Size / rate));
                int last = Math.Min(magnitudes.Length - 1, Math.Max(first, (int)Math.Ceiling(high * Size / rate) - 1));
                double amplitude = 0;
                for (int i = first; i <= last; i++) amplitude = Math.Max(amplitude, magnitudes[i]);
                frame.Bands[band] = Level(amplitude);
                if (high <= 250) frame.Bass = Math.Max(frame.Bass, frame.Bands[band]);
                else if (high <= 2500) frame.Mid = Math.Max(frame.Mid, frame.Bands[band]);
                else frame.Treble = Math.Max(frame.Treble, frame.Bands[band]);
            }
            return frame;
        }
    }

    public static class Pcm
    {
        public static double Decode(byte[] data, int offset, int bits, bool floating)
        {
            double value;
            if (floating && bits == 32) value = BitConverter.ToSingle(data, offset);
            else if (floating && bits == 64) value = BitConverter.ToDouble(data, offset);
            else if (!floating && bits == 16) value = BitConverter.ToInt16(data, offset) / 32768.0;
            else if (!floating && bits == 24)
            {
                int raw = data[offset] | data[offset + 1] << 8 | data[offset + 2] << 16;
                if ((raw & 0x800000) != 0) raw |= unchecked((int)0xff000000);
                value = raw / 8388608.0;
            }
            else if (!floating && bits == 32) value = BitConverter.ToInt32(data, offset) / 2147483648.0;
            else if (!floating && bits == 8) value = (data[offset] - 128) / 128.0;
            else throw new NotSupportedException("Unsupported speaker format");
            return Double.IsNaN(value) || Double.IsInfinity(value) ? 0 : Math.Max(-1, Math.Min(1, value));
        }
    }

    public static class Loopback
    {
        static readonly CultureInfo Invariant = CultureInfo.InvariantCulture;
        static readonly DateTime Epoch = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
        static bool ParentAlive(int pid)
        {
            if (pid == 0) return true;
            try { using (Process process = Process.GetProcessById(pid)) return !process.HasExited; }
            catch (ArgumentException) { return false; }
        }
        static void Check(int result) { if (result < 0) Marshal.ThrowExceptionForHR(result); }
        static void Release(object value) { if (value != null && Marshal.IsComObject(value)) Marshal.ReleaseComObject(value); }
        static string Stamp() { return (DateTime.UtcNow - Epoch).TotalMilliseconds.ToString("F0", Invariant); }
        static void Write(string path, string payload)
        {
            try
            {
                string temp = path + ".tmp";
                File.WriteAllText(temp, payload, new UTF8Encoding(false));
                if (File.Exists(path)) File.Replace(temp, path, null);
                else File.Move(temp, path);
            }
            catch (IOException) { }
        }
        static void ArrayJson(StringBuilder builder, double[] values)
        {
            builder.Append('[');
            for (int i = 0; i < values.Length; i++)
            {
                if (i > 0) builder.Append(',');
                builder.Append(values[i].ToString("F4", Invariant));
            }
            builder.Append(']');
        }
        static void Publish(string path, SpectrumFrame frame, double beat, int rate, long sequence)
        {
            StringBuilder text = new StringBuilder(2200);
            text.Append("{\"ready\":true,\"timestamp\":").Append(Stamp());
            text.Append(",\"sequence\":").Append(sequence).Append(",\"sample_rate\":").Append(rate);
            text.Append(",\"rms\":").Append(frame.Rms.ToString("F6", Invariant));
            text.Append(",\"peak\":").Append(frame.Peak.ToString("F6", Invariant));
            text.Append(",\"bass\":").Append(frame.Bass.ToString("F4", Invariant));
            text.Append(",\"mid\":").Append(frame.Mid.ToString("F4", Invariant));
            text.Append(",\"treble\":").Append(frame.Treble.ToString("F4", Invariant));
            text.Append(",\"beat\":").Append(beat.ToString("F4", Invariant));
            text.Append(",\"bands\":"); ArrayJson(text, frame.Bands);
            text.Append(",\"wave\":"); ArrayJson(text, frame.Wave);
            text.Append('}'); Write(path, text.ToString());
        }

        public static void Run(string path, int parentPid)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            while (ParentAlive(parentPid))
            {
                try { Capture(path, parentPid); }
                catch (Exception ex)
                {
                    string message = ex.Message.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", " ").Replace("\n", " ");
                    Write(path, "{\"ready\":false,\"timestamp\":" + Stamp() + ",\"error\":\"" + message + "\"}");
                    Thread.Sleep(1000);
                }
            }
        }

        static void Capture(string path, int parentPid)
        {
            IMMDeviceEnumerator enumerator = null;
            IMMDevice device = null;
            IAudioClient client = null;
            IAudioCaptureClient capture = null;
            IntPtr format = IntPtr.Zero;
            bool started = false;
            try
            {
                enumerator = (IMMDeviceEnumerator)(new DeviceEnumerator());
                Check(enumerator.GetDefaultAudioEndpoint(0, 1, out device));
                string deviceId; Check(device.GetId(out deviceId));
                Guid iid = typeof(IAudioClient).GUID;
                object iface; Check(device.Activate(ref iid, 23, IntPtr.Zero, out iface));
                client = (IAudioClient)iface;
                Check(client.GetMixFormat(out format));
                int tag = (ushort)Marshal.ReadInt16(format, 0);
                int channels = (ushort)Marshal.ReadInt16(format, 2);
                int rate = Marshal.ReadInt32(format, 4);
                int align = (ushort)Marshal.ReadInt16(format, 12);
                int bits = (ushort)Marshal.ReadInt16(format, 14);
                if (tag == 0xfffe)
                {
                    Guid subtype = (Guid)Marshal.PtrToStructure(IntPtr.Add(format, 24), typeof(Guid));
                    if (subtype == new Guid("00000003-0000-0010-8000-00aa00389b71")) tag = 3;
                    else if (subtype == new Guid("00000001-0000-0010-8000-00aa00389b71")) tag = 1;
                }
                if ((tag != 1 && tag != 3) || channels < 1 || rate < 8000 || align < channels * (bits / 8))
                    throw new NotSupportedException("Unsupported speaker format");
                Guid session = Guid.Empty;
                Check(client.Initialize(0, 0x00020000, 1200000, 0, format, ref session));
                iid = typeof(IAudioCaptureClient).GUID;
                Check(client.GetService(ref iid, out iface)); capture = (IAudioCaptureClient)iface;
                Check(client.Start()); started = true;
                double[] left = new double[SpectrumMath.Size], right = new double[SpectrumMath.Size];
                double[] samples = new double[SpectrumMath.Size];
                int cursor = 0;
                long sequence = 0;
                Stopwatch clock = Stopwatch.StartNew();
                long lastAudio = -1000, lastPublish = -1000, lastDeviceCheck = 0, lastBeat = -1000;
                double bassAverage = 0, beat = 0;
                byte[] bytes = new byte[0];
                while (true)
                {
                    long now = clock.ElapsedMilliseconds;
                    if (now - lastDeviceCheck >= 2000)
                    {
                        if (!ParentAlive(parentPid)) return;
                        IMMDevice current = null;
                        try
                        {
                            Check(enumerator.GetDefaultAudioEndpoint(0, 1, out current));
                            string currentId; Check(current.GetId(out currentId));
                            if (currentId != deviceId) return;
                        }
                        finally { Release(current); }
                        lastDeviceCheck = now;
                    }
                    uint available; Check(capture.GetNextPacketSize(out available));
                    while (available > 0)
                    {
                        IntPtr data; uint frames, flags; ulong position, counter;
                        Check(capture.GetBuffer(out data, out frames, out flags, out position, out counter));
                        try
                        {
                            if ((flags & 1) != 0) { Array.Clear(left, 0, left.Length); Array.Clear(right, 0, right.Length); }
                            bool silent = (flags & 2) != 0;
                            int count = checked((int)frames * align);
                            if (!silent)
                            {
                                if (bytes.Length < count) bytes = new byte[count];
                                Marshal.Copy(data, bytes, 0, count);
                            }
                            for (int i = 0; i < frames; i++)
                            {
                                int offset = i * align;
                                left[cursor] = silent ? 0 : Pcm.Decode(bytes, offset, bits, tag == 3);
                                right[cursor] = silent ? 0 : Pcm.Decode(bytes, offset + (channels > 1 ? bits / 8 : 0), bits, tag == 3);
                                cursor = (cursor + 1) % samples.Length;
                            }
                            lastAudio = now;
                        }
                        finally { Check(capture.ReleaseBuffer(frames)); }
                        Check(capture.GetNextPacketSize(out available));
                    }
                    if (now - lastPublish >= 30)
                    {
                        if (now - lastAudio > 120) { Array.Clear(left, 0, left.Length); Array.Clear(right, 0, right.Length); }
                        // Analyze the stronger stereo channel, so phase-inverted audio does not cancel.
                        double energyL = 0, energyR = 0;
                        for (int i = 0; i < left.Length; i++) { energyL += left[i] * left[i]; energyR += right[i] * right[i]; }
                        double[] channel = energyL >= energyR ? left : right;
                        for (int i = 0; i < samples.Length; i++) samples[i] = channel[(cursor + i) % samples.Length];
                        SpectrumFrame frame = SpectrumMath.Analyze(samples, rate);
                        double delta = lastPublish < 0 ? .03 : (now - lastPublish) / 1000.0;
                        beat *= Math.Exp(-delta / .18);
                        if (bassAverage > .0001 && frame.BassEnergy > bassAverage * 1.35 && frame.Rms > .001 && now - lastBeat > 180)
                        { beat = 1; lastBeat = now; }
                        bassAverage = bassAverage == 0 ? frame.BassEnergy : bassAverage * .95 + frame.BassEnergy * .05;
                        Publish(path, frame, beat, rate, ++sequence);
                        lastPublish = now;
                    }
                    Thread.Sleep(8);
                }
            }
            finally
            {
                if (started && client != null) client.Stop();
                if (format != IntPtr.Zero) Marshal.FreeCoTaskMem(format);
                Release(capture); Release(client); Release(device); Release(enumerator);
            }
        }
    }
}
