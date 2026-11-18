import { Notice } from "@/components/Notice";

export default function NotFound() {
  return (
    <Notice title="Page not found" action={{ href: "/", label: "Go to search" }}>
      <p>That page does not exist.</p>
    </Notice>
  );
}
