import { Board } from "@/components/board";
import { Sidebar } from "@/components/sidebar";

export default function Home() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <Board className="flex-1" />
    </div>
  );
}
