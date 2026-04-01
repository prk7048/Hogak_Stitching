import { Navigate, Route, Routes } from "react-router-dom";

import { ProjectPage } from "./pages/ProjectPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<ProjectPage />} />
      <Route path="/run" element={<Navigate to="/" replace />} />
      <Route path="/validate" element={<Navigate to="/" replace />} />
      <Route path="/dashboard" element={<Navigate to="/" replace />} />
      <Route path="/validation" element={<Navigate to="/" replace />} />
      <Route path="/outputs" element={<Navigate to="/" replace />} />
      <Route path="/artifacts" element={<Navigate to="/" replace />} />
      <Route path="/geometry-compare" element={<Navigate to="/" replace />} />
      <Route path="/bakeoff" element={<Navigate to="/" replace />} />
      <Route path="/calibration/*" element={<Navigate to="/" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
