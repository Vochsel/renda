"use client";

import { useActionState, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createKeyAction, type CreateKeyState } from "./actions";

export function CreateKeyForm() {
  const [state, action, pending] = useActionState<CreateKeyState, FormData>(createKeyAction, {});
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-3">
      <form action={action} className="flex gap-2">
        <Input name="name" placeholder="Key name, e.g. claude-code laptop" className="max-w-sm" />
        <Button type="submit" disabled={pending}>{pending ? "Creating…" : "Create key"}</Button>
      </form>
      {state.apiKey && (
        <Alert>
          <AlertTitle>Copy this key now — it won&apos;t be shown again</AlertTitle>
          <AlertDescription className="mt-2 flex flex-wrap items-center gap-2">
            <code className="bg-muted rounded px-2 py-1 font-mono text-xs break-all">{state.apiKey}</code>
            <Button size="sm" variant="outline" onClick={() => { void navigator.clipboard.writeText(state.apiKey!); setCopied(true); }}>
              {copied ? "Copied" : "Copy"}
            </Button>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
