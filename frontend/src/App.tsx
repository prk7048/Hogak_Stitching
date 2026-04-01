import { Navigate, Route, Routes } from "react-router-dom";

import { ProjectPage } from "./pages/ProjectPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<ProjectPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
