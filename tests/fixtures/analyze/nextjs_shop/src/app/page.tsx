import { Button } from "@/components/Button";

export default function Home() {
  return <main><Button label="Shop" onClick={() => fetch("/api/products")} /></main>;
}
