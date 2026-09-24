import { describe, expect, it } from "vitest";
import { compact, day, featureLabel, monthShort, need, NEED_GROUP, NEED_LABEL, pct, shortId } from "./format";

describe("format", () => {
  it("keeps every month at three letters", () => {
    expect(monthShort("2026-09-01")).toBe("Sep");
    expect(day("2026-09-24")).toBe("24 Sep 2026");
  });
  it("shows a need estimate at or below zero as above the poverty line", () => {
    expect(need(-513)).toBe("above the line");
    expect(need(0)).toBe("above the line");
    expect(need(3185.4)).toBe("3,185");
  });
  it("compacts money", () => {
    expect(compact(15_637_551)).toBe("15.6M");
    expect(compact(3_348_147)).toBe("3.35M");
    expect(compact(82_000)).toBe("82k");
  });
  it("puts every need category in a support group", () => {
    expect(Object.keys(NEED_LABEL).every((k) => NEED_GROUP[k])).toBe(true);
    expect(NEED_GROUP.funeral).toBe("bereavement");
  });
  it("handles edge cases", () => {
    expect(pct(1, 0)).toBe("—");
    expect(shortId("3037abcd-0000")).toBe("HH-3037");
    expect(featureLabel("shock_job_loss_12m")).toBe("Shock: job loss");
  });
});
