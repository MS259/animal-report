import React, { useState } from "react";
import {
  StyleSheet,
  Text,
  View,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
} from "react-native";
import * as Location from "expo-location";

// TODO: Update this when backend is accessible from the device
//const BACKEND_URL = "http://192.168.1.236/report";
// Backend base URL (set per environment). Fallback to production.
const BACKEND_BASE_URL =
  process.env.EXPO_PUBLIC_BACKEND_URL || "https://animal-report-api.onrender.com";

const BACKEND_URL = `${BACKEND_BASE_URL.replace(/\/+$/, "")}/report`;



export default function HomeScreen() {
  const [loading, setLoading] = useState(false);

  const requestAndSend = async (type: "dead" | "injured") => {
    //console.log("BUTTON PRESSED, type =", type);
    //console.log("BACKEND_URL =", BACKEND_URL);
    try {
      setLoading(true);

      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") {
        Alert.alert(
          "Permission needed",
          "Location access is required to report animals."
        );
        setLoading(false);
        return;
      }

      const loc = await Location.getCurrentPositionAsync({});
      const payload = {
        type,
        latitude: loc.coords.latitude,
        longitude: loc.coords.longitude,
        timestamp: new Date().toISOString(),
      };

      const resp = await fetch(BACKEND_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      //console.log("ABOUT TO FETCH...");
      //console.log("FETCH status =", resp.status);
      const text = await resp.text();
      //console.log("FETCH response body =", text);

      if (!resp.ok) {
        throw new Error(`Server error ${resp.status}: ${text}`);
      }

      Alert.alert("Thank you", "Report sent successfully.");
    } catch (err) {
      console.log(err);
      Alert.alert("Error", "Could not send report.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.icon}>🐾</Text>
        <Text style={styles.title}>Report Animal</Text>
        <Text style={styles.subtitle}>One tap to help wildlife</Text>
      </View>

      <View style={styles.buttons}>
        <ReportButton
          label="Sleeping Animal"
          onPress={() => requestAndSend("dead")}
          color="#ffffffcc"
        />
        <ReportButton
          label="Injured Animal"
          onPress={() => requestAndSend("injured")}
          color="#ffffffcc"
        />
      </View>

      {loading && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" />
        </View>
      )}
    </View>
  );
}

function ReportButton({
  label,
  onPress,
  color,
}: {
  label: string;
  onPress: () => void;
  color: string;
}) {
  return (
    <TouchableOpacity
      style={[styles.button, { backgroundColor: color }]}
      onPress={onPress}
    >
      <Text style={styles.buttonText}>{label}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    paddingTop: 80,
    paddingHorizontal: 24,
    backgroundColor: "#1f8ff0",
  },
  header: {
    alignItems: "center",
    marginBottom: 40,
  },
  icon: {
    fontSize: 48,
  },
  title: {
    marginTop: 8,
    fontSize: 28,
    fontWeight: "bold",
    color: "#fff",
  },
  subtitle: {
    marginTop: 4,
    fontSize: 14,
    color: "#e0f2ff",
  },
  buttons: {
    gap: 16,
  },
  button: {
    paddingVertical: 18,
    paddingHorizontal: 16,
    borderRadius: 16,
    alignItems: "center",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 6,
    elevation: 4,
  },
  buttonText: {
    fontSize: 18,
    fontWeight: "600",
    color: "#000",
  },
  loadingOverlay: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 40,
    alignItems: "center",
  },
});
