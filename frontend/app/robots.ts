import { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: ["/", "/privacy", "/terms", "/help", "/stop"],
      disallow: ["/dashboard", "/quiz/", "/auth/", "/unsubscribe"],
    },
    sitemap: "https://stackd.chat/sitemap.xml",
  };
}
