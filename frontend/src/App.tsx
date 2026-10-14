import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Accounts from "./routes/Accounts";
import Dashboard from "./routes/Dashboard";
import Login from "./routes/Login";
import Settings from "./routes/Settings";
import Setup from "./routes/Setup";
import Transactions from "./routes/Transactions";
import Business from "./routes/Business";
import Taxes from "./routes/Taxes";
import Investments from "./routes/Investments";
import NetWorth from "./routes/NetWorth";
import CashFlow from "./routes/CashFlow";
import Subscriptions from "./routes/Subscriptions";
import Reports from "./routes/Reports";
import CollectrDrop from "./routes/CollectrDrop";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/setup" element={<Setup />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/transactions" element={<Transactions />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/cashflow" element={<CashFlow />} />
        <Route path="/subscriptions" element={<Subscriptions />} />
        <Route path="/business" element={<Business />} />
        <Route path="/taxes" element={<Taxes />} />
        <Route path="/investments" element={<Investments />} />
        <Route path="/networth" element={<NetWorth />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/collectr-drop" element={<CollectrDrop />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
