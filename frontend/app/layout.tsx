import type { Metadata } from "next";
import { Fredoka, Nunito, Inter, DM_Serif_Display, DM_Sans } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-inter",
  display: "swap",
});

const fredoka = Fredoka({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-fredoka",
  display: "swap",
});

const nunito = Nunito({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800", "900"],
  variable: "--font-nunito",
  display: "swap",
});

const dmSerifDisplay = DM_Serif_Display({
  subsets: ["latin"],
  weight: "400",
  style: ["normal", "italic"],
  variable: "--font-dm-serif",
  display: "swap",
});

const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-dm-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "stackd — Your coach is texting you",
  description:
    "Pick a celebrity coach. Share your goals. Get your first text in 60 seconds. No app. No login. Just text.",
  metadataBase: new URL("https://stackd.chat"),
  icons: { icon: "/icon.svg" },
  openGraph: {
    title: "stackd — Your coach is texting you",
    description:
      "Pick a celebrity coach. Share your goals. Get your first text in 60 seconds.",
    url: "https://stackd.chat",
    siteName: "stackd",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "stackd" }],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "stackd — Your coach is texting you",
    description:
      "Pick a celebrity coach. Share your goals. Get your first text in 60 seconds.",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${fredoka.variable} ${nunito.variable} ${dmSerifDisplay.variable} ${dmSans.variable}`}>
      <body className="overflow-x-hidden antialiased">
        {children}
      </body>
    </html>
  );
}
