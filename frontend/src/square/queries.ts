import { api, ApiError } from "../api/client";

export const squareConnectionQuery = {
  queryKey: ["square-connection"],
  queryFn: async () => {
    const { data, error, response } = await api.GET("/square/connection");
    if (!response.ok || !data) throw new ApiError(response, error);
    return data;
  },
};
