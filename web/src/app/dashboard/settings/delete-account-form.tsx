"use client";

import { useActionState, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { DELETE_CONFIRMATION } from "@/lib/legal";
import { deleteAccountAction, type DeleteState } from "./actions";

export function DeleteAccountForm() {
  const [state, action, pending] = useActionState<DeleteState, FormData>(deleteAccountAction, {});
  const [typed, setTyped] = useState("");
  const ready = typed.trim().toLowerCase() === DELETE_CONFIRMATION;
  return (
    <form action={action} className="space-y-3">
      {state.error && <Alert variant="destructive"><AlertDescription>{state.error}</AlertDescription></Alert>}
      <div className="space-y-2">
        <Label htmlFor="confirm">Type <span className="font-mono">{DELETE_CONFIRMATION}</span> to confirm</Label>
        <Input id="confirm" name="confirm" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} className="max-w-sm" />
      </div>
      <Button type="submit" variant="destructive" disabled={!ready || pending}>
        {pending ? "Deleting…" : "Delete account"}
      </Button>
    </form>
  );
}
