import { Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { ArtifactsPage } from "./pages/ArtifactsPage";
import { CalibrationAssistedPage } from "./pages/CalibrationAssistedPage";
import { CalibrationReviewPage } from "./pages/CalibrationReviewPage";
import { CalibrationStartPage } from "./pages/CalibrationStartPage";
import { CalibrationStitchReviewPage } from "./pages/CalibrationStitchReviewPage";
import { DashboardPage } from "./pages/DashboardPage";
import { GeometryComparePage } from "./pages/GeometryComparePage";
import { OutputsPage } from "./pages/OutputsPage";
import { ValidationPage } from "./pages/ValidationPage";

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/calibration/start" element={<CalibrationStartPage />} />
        <Route path="/calibration/assisted" element={<CalibrationAssistedPage />} />
        <Route path="/calibration/review" element={<CalibrationReviewPage />} />
        <Route path="/calibration/stitch-review" element={<CalibrationStitchReviewPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/validation" element={<ValidationPage />} />
        <Route path="/geometry-compare" element={<GeometryComparePage />} />
        <Route path="/outputs" element={<OutputsPage />} />
        <Route path="/artifacts" element={<ArtifactsPage />} />
      </Routes>
    </Shell>
  );
}
