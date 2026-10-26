import { createFileRoute, redirect } from "@tanstack/react-router";

// New accounts are not self-service; route visitors to access-token login.
export const Route = createFileRoute("/sign-up")({
  beforeLoad: () => { throw redirect({ to: "/sign-in" }); },
});
