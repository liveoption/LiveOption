import { createFileRoute } from "@tanstack/react-router";
import { SignInPage } from "./sign-in";

// Preserve legacy sign-in deep links and render the token form.
export const Route = createFileRoute("/sign-in/$")({
  component: SignInPage,
});
