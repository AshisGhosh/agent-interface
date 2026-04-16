import { Board } from "@/components/board";
import { ProjectsProvider } from "@/components/projects-provider";
import { Sidebar } from "@/components/sidebar";

export default function Home() {
  return (
    <ProjectsProvider>
      <div className="flex h-screen">
        <Sidebar />
        <Board className="flex-1" />
      </div>
    </ProjectsProvider>
  );
}
