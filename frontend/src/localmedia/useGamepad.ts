import { useEffect, useRef, useState } from "react";

export type GamepadAction = "left" | "right" | "up" | "down" | "open" | "back" | "previous" | "next" | "directories";

// Only standard mappings are interpreted; other controllers keep their browser mapping.
export function useGamepad(onAction: (action: GamepadAction) => void) {
  const callback = useRef(onAction);
  callback.current = onAction;
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!navigator.getGamepads) return;
    let frame = 0;
    let previous = new Set<GamepadAction>();
    let controller = "";
    const repeats = new Map<GamepadAction, number>();
    const tick = (now: number) => {
      let pad: Gamepad | undefined;
      try {
        pad = Array.from(navigator.getGamepads()).find(p => p?.connected && p.mapping === "standard") ?? undefined;
      } catch {
        setConnected(false);
        return;
      }
      setConnected(!!pad);
      if (controller !== `${pad?.index}:${pad?.id}`) {
        previous.clear();
        repeats.clear();
        controller = `${pad?.index}:${pad?.id}`;
      }
      const current = new Set<GamepadAction>();
      const pressed = (i: number) => !!pad?.buttons[i]?.pressed;
      if (pad) {
        const x = pad.axes[0] || 0, y = pad.axes[1] || 0;
        if (pressed(14) || x < -0.55) current.add("left");
        if (pressed(15) || x > 0.55) current.add("right");
        if (pressed(12) || y < -0.55) current.add("up");
        if (pressed(13) || y > 0.55) current.add("down");
        if (pressed(0)) current.add("open");
        if (pressed(1)) current.add("back");
        if (pressed(2)) current.add("directories");
        if (pressed(4)) current.add("previous");
        if (pressed(5)) current.add("next");
      }
      const active = document.visibilityState === "visible" && document.hasFocus();
      for (const action of current) {
        const direction = ["left", "right", "up", "down"].includes(action);
        if (!previous.has(action)) {
          if (active) callback.current(action);
          repeats.set(action, now + 400);
        } else if (direction && now >= (repeats.get(action) ?? Infinity)) {
          if (active) callback.current(action);
          repeats.set(action, now + 160);
        }
      }
      previous = current;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);
  return connected;
}
