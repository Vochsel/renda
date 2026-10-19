import { notFound } from "next/navigation";
import { Gallery } from "@/components/gallery/gallery";
import { RecentStrip } from "@/components/gallery/recent-strip";
import { loadFixturePage } from "./actions";
import { fixturePage } from "./fixtures";

/** Dev-only preview of the dashboard gallery with fixture data (404 in production builds). */
export default function DevGalleryPage() {
  if (process.env.NODE_ENV === "production") notFound();
  const initial = fixturePage(null, 24);
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 md:pl-[13rem]">
      <h1 className="mb-4 text-xl font-semibold">Gallery (fixtures)</h1>
      <div className="mb-6"><RecentStrip items={initial.items.slice(0, 10)} /></div>
      <Gallery initial={initial} loadMore={loadFixturePage} />
    </div>
  );
}
