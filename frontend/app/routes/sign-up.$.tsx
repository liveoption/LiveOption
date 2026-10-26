import { createFileRoute, redirect } from "@tanstack/react-router";

// Preserve legacy sign-up deep links and route them to token login.
export const Route = createFileRoute("/sign-up/$")({
  beforeLoad: () => { throw redirect({ to: "/sign-in" }); },
});
