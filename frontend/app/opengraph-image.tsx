import { ImageResponse } from "next/og";

export const runtime = "edge";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OGImage() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "#F5EFE6",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 24,
        }}
      >
        <div
          style={{
            fontSize: 96,
            fontWeight: 700,
            color: "#1A1612",
            letterSpacing: "-0.04em",
          }}
        >
          stackd
        </div>
        <div
          style={{
            fontSize: 36,
            color: "#6B5E4E",
            textAlign: "center",
            maxWidth: 700,
          }}
        >
          Your coach is texting you.
        </div>
      </div>
    ),
    { ...size }
  );
}
