import type { MouseEventHandler } from "react";

import { calibrationImageUrl } from "../lib/api";

type CalibrationImageProps = {
  src: string;
  alt: string;
  clickable?: boolean;
  onClick?: MouseEventHandler<HTMLImageElement>;
};

function placeholder(label: string): string {
  return (
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'><rect width='100%' height='100%' fill='#0b0f17'/><text x='50%' y='50%' fill='#d9d2c2' font-size='28' text-anchor='middle'>${label}</text></svg>`,
    )
  );
}

export function CalibrationImage({ src, alt, clickable = false, onClick }: CalibrationImageProps) {
  return (
    <img
      className={`preview-image calibration-image${clickable ? " clickable" : ""}`}
      src={src ? calibrationImageUrl(src) : placeholder(alt)}
      alt={alt}
      onClick={onClick}
      onError={(event) => {
        event.currentTarget.src = placeholder(alt);
      }}
    />
  );
}
