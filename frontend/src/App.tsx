import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import ApplicationDetail from "./pages/ApplicationDetail";
import Applications from "./pages/Applications";
import Cycles from "./pages/Cycles";
import Dashboard from "./pages/Dashboard";
import Home from "./pages/Home";
import Households from "./pages/Households";
import Login from "./pages/Login";
import Models from "./pages/Models";
import NewApplication from "./pages/NewApplication";
import Review from "./pages/Review";
import { useAuth } from "./state/auth";

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <div className="login"><Loading /></div>;
  if (!user) return <Login />;
  const admin = user.role === "admin";
  const adminOnly = (el: ReactNode) => (admin ? el : <Navigate to="/" replace />);
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={admin ? <Dashboard /> : <Home />} />
          <Route path="applications" element={<Applications />} />
          <Route path="applications/new" element={<NewApplication />} />
          <Route path="applications/:id" element={<ApplicationDetail />} />
          <Route path="review" element={<Review />} />
          <Route path="households" element={<Households />} />
          <Route path="cycles" element={adminOnly(<Cycles />)} />
          <Route path="models" element={adminOnly(<Models />)} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
