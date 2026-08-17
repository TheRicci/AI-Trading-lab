"use client";

import { useEffect, useRef, useState } from "react";
import { animate } from "framer-motion";

export function AnimatedValue({ value, className }: { value: number; className?: string }) {
  const [display, setDisplay] = useState(value);
  const prevValue = useRef(value);

  useEffect(() => {
    const controls = animate(prevValue.current, value, {
      duration: 0.6,
      ease: "easeOut",
      onUpdate: (v) => setDisplay(v),
    });
    prevValue.current = value;
    return () => controls.stop();
  }, [value]);

  return (
    <span className={className}>
      $
      {display.toLocaleString(undefined, {
        maximumFractionDigits: 0,
      })}
    </span>
  );
}
