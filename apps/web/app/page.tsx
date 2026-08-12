import { ChatShell } from "@/components/chat-shell";

/**
 * The chat surface.
 *
 * Client-rendered, because the interesting part is a stream: the reply arrives token by
 * token over SSE and there is nothing useful to render on the server first.
 */
export default function HomePage() {
  return <ChatShell />;
}
