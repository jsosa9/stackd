import { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: "https://stackd.chat", lastModified: new Date(), changeFrequency: "weekly", priority: 1 },
    { url: "https://stackd.chat/privacy", lastModified: new Date(), changeFrequency: "monthly", priority: 0.3 },
    { url: "https://stackd.chat/terms", lastModified: new Date(), changeFrequency: "monthly", priority: 0.3 },
    { url: "https://stackd.chat/help", lastModified: new Date(), changeFrequency: "monthly", priority: 0.4 },
  ];
}
