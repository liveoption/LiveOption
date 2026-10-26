import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { changeLocale, translate, resolveLocale, useLocale } from "../../app/lib/locale";
import { japanese } from "../../app/lib/locales/ja";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";

beforeEach(() => { localStorage.clear(); changeLocale("en"); });

describe("locale", () => {
  it("uses explicit preferences, then the browser language", () => {
    expect(resolveLocale("ja", "zh-CN")).toBe("ja");
    expect(resolveLocale("en", "ja-JP")).toBe("en");
    expect(resolveLocale(null, "ja-JP")).toBe("ja");
    expect(resolveLocale(null, "zh-HK")).toBe("zh");
    expect(resolveLocale("unsupported", "fr-FR")).toBe("en");
  });
  it("keeps interpolation values and unknown source content unchanged", () => {
    const ja = (english: string, ...values: unknown[]) => translate("ja", english, ...values);
    expect(ja("Every {0} min", 15)).toBe("15分ごと");
    expect(ja("Decisions · {0}/{1}", 2, 18)).toBe("判断記録：2/18");
    expect(ja("Decisions · {0}/{1}", "{cash}", 18)).toBe("判断記録：{cash}/18");
    expect(ja("Trade options on {universe}." )).toBe("Trade options on {universe}.");
    expect(ja("NameError: templates is not defined")).toBe("NameError: templates is not defined");
  });
  it("retains every numbered interpolation slot in the Japanese catalog", () => {
    for (const [english, translation] of Object.entries(japanese)) {
      expect(translation.trim(), english).not.toBe("");
      expect(translation.match(/\{\d+\}/g)?.sort() ?? [], english)
        .toEqual(english.match(/\{\d+\}/g)?.sort() ?? []);
    }
  });
  it("updates all consumers and persists the choice", () => {
    function Label() { const { locale, ja } = useLocale(); return <span>{locale}: {ja("Run logs")}</span>; }
    render(<><Label /><Label /></>);
    act(() => changeLocale("ja"));
    expect(screen.getAllByText("ja: 実行ログ")).toHaveLength(2);
    expect(localStorage.getItem("lo-locale")).toBe("ja");
    expect(document.documentElement.lang).toBe("ja");
    act(() => changeLocale("zh"));
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(translate("zh", "Run logs")).toBe("Run logs");
  });
  it("hydrates English server HTML with a saved Japanese preference without mismatches", async () => {
    function Label() { const { ja } = useLocale(); return <span>{ja("Enter access token")}</span>; }
    localStorage.setItem("lo-locale", "ja");
    const container = document.createElement("div");
    container.innerHTML = renderToString(<Label />);
    expect(container.textContent).toBe("Enter access token");
    document.body.appendChild(container);
    const errors: unknown[] = [];
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => { root = hydrateRoot(container, <Label />, { onRecoverableError: (error) => errors.push(error) }); });
    expect(container.textContent).toBe("アクセストークンを入力");
    expect(errors).toEqual([]);
    act(() => root.unmount());
    container.remove();
  });
});
