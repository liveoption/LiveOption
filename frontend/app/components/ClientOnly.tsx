import { useEffect, useState, type ReactNode } from "react";

/**
 * Renders children only after the component has mounted on the client.
 *
 * Used to defer rendering of components that produce SSR/CSR markup drift
 * (e.g. Clerk's `<UserButton />`, which reads session state that isn't
 * available during SSR). Prevents React hydration warnings.
 */
export function ClientOnly({
  children,
  fallback = null,
}: {
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return <>{fallback}</>;
  return <>{children}</>;
}

export default ClientOnly;
