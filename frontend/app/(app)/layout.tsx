import { AppShell } from "@/components/AppShell";

export default function ProtectedLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <AppShell>{children}</AppShell>;
}
