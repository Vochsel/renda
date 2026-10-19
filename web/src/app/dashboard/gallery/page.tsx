import type { Metadata } from "next";
import { requireAccount } from "@/lib/auth";
import { GALLERY_PAGE_SIZE, listOutputs } from "@/lib/outputs";
import { Code } from "@/components/code";
import { Gallery } from "@/components/gallery/gallery";
import { loadGalleryPage } from "./actions";

export const metadata: Metadata = { title: "Gallery" };

export default async function GalleryPage() {
  const { org } = await requireAccount();
  const initial = await listOutputs(org.id, { limit: GALLERY_PAGE_SIZE });
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Gallery</h1>
      <Gallery
        initial={initial}
        loadMore={loadGalleryPage}
        empty={
          <div className="space-y-3">
            <p className="text-muted-foreground text-sm">Your renders will show up here.</p>
            <Code>{`npx @rendahq/cli render model.glb        # or: uvx renda render model.glb --cloud`}</Code>
          </div>
        }
      />
    </div>
  );
}
