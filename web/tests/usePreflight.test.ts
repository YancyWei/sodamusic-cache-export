import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePreflight } from "@/hooks/usePreflight";

vi.mock("@/lib/api", () => ({
  getPreflightStatus: vi.fn().mockResolvedValue({
    ready: true,
    cacheDir: "/tmp/cache",
    outputDir: "/tmp/output",
    deviceNode: "/tmp/device.node",
    sources: { exportable: 10 },
  }),
}));

import { getPreflightStatus } from "@/lib/api";

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", {
    value: state,
    configurable: true,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("usePreflight visibility-aware polling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(getPreflightStatus).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetches immediately on mount", () => {
    renderHook(() => usePreflight(5000));
    expect(getPreflightStatus).toHaveBeenCalledTimes(1);
  });

  it("polls at the specified interval when visible", async () => {
    renderHook(() => usePreflight(5000));

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(getPreflightStatus).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(getPreflightStatus).toHaveBeenCalledTimes(3);
  });

  it("stops polling when page becomes hidden", async () => {
    renderHook(() => usePreflight(5000));

    await act(async () => {
      setVisibility("hidden");
    });

    const countAfterHide = getPreflightStatus.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    expect(getPreflightStatus).toHaveBeenCalledTimes(countAfterHide);
  });

  it("fetches immediately and resumes polling when page becomes visible", async () => {
    renderHook(() => usePreflight(5000));

    await act(async () => {
      setVisibility("hidden");
    });

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    const countAfterHidden = getPreflightStatus.mock.calls.length;

    await act(async () => {
      setVisibility("visible");
    });

    expect(getPreflightStatus.mock.calls.length).toBeGreaterThan(countAfterHidden);

    const countAfterVisible = getPreflightStatus.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(getPreflightStatus.mock.calls.length).toBeGreaterThan(countAfterVisible);
  });

  it("does not start polling if page is hidden on mount", async () => {
    setVisibility("hidden");

    renderHook(() => usePreflight(5000));

    const countAfterMount = getPreflightStatus.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    expect(getPreflightStatus).toHaveBeenCalledTimes(countAfterMount);
  });

  it("starts polling when hidden page becomes visible", async () => {
    setVisibility("hidden");

    renderHook(() => usePreflight(5000));

    await act(async () => {
      setVisibility("visible");
    });

    const countAfterVisible = getPreflightStatus.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(getPreflightStatus.mock.calls.length).toBeGreaterThan(countAfterVisible);
  });
});
