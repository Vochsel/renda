import { notFound } from "next/navigation";
import { Playground } from "@/components/playground/playground";
import { devEstimate, devGetJob, devRegisterUploads, devSubmit } from "./actions";

/** Dev-only preview of the dashboard playground with fake uploads/jobs (404 in production builds). */
export default function DevPlaygroundPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-6">
      <h1 className="mb-6 text-xl font-semibold">Playground (fixtures)</h1>
      <Playground actions={{ registerUploads: devRegisterUploads, estimate: devEstimate, submit: devSubmit, getJob: devGetJob }} />
    </div>
  );
}
