import { requireAccount } from "@/lib/auth";
import { listApiKeys } from "@/lib/accounts";
import { fmtAgo } from "@/lib/format";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CreateKeyForm } from "./create-key-form";
import { RevokeButton } from "./revoke-button";

export default async function KeysPage() {
  const { org } = await requireAccount();
  const keys = await listApiKeys(org.id);
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">API keys</h1>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">New key</CardTitle>
          <CardDescription>Keys act for the whole workspace. Agents can also mint their own via <span className="font-mono">renda login</span> or the magic-code flow.</CardDescription>
        </CardHeader>
        <CardContent><CreateKeyForm /></CardContent>
      </Card>
      <Card>
        <CardContent className="pt-6">
          {keys.length === 0 ? <p className="text-muted-foreground text-sm">No active keys.</p> : (
            <Table>
              <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Key</TableHead><TableHead>Last used</TableHead><TableHead>Created</TableHead><TableHead /></TableRow></TableHeader>
              <TableBody>
                {keys.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell className="text-sm">{k.name}</TableCell>
                    <TableCell className="font-mono text-xs">{k.prefix}…</TableCell>
                    <TableCell className="text-muted-foreground text-xs">{k.lastUsedAt ? fmtAgo(k.lastUsedAt) : "never"}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">{fmtAgo(k.createdAt)}</TableCell>
                    <TableCell className="text-right"><RevokeButton id={k.id} name={k.name} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
